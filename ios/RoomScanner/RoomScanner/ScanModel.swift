import ARKit
import CoreImage
import ImageIO
import RealityKit
import RoomPlan
import SwiftUI
import UniformTypeIdentifiers

/// Drives one floor capture: scan rooms one after another in a single ARSession,
/// merge them with StructureBuilder, then write the files the Python converter reads.
@MainActor
final class ScanModel: NSObject, ObservableObject, RoomCaptureViewDelegate {
    let captureView: RoomCaptureView
    @Published private(set) var rooms: [CapturedRoom] = []
    /// The last processed room, not yet kept. Start discards it (rescan), Add/Finish keep it.
    @Published private(set) var pending: CapturedRoom?
    @Published private(set) var structure: CapturedStructure?
    @Published private(set) var exported: [URL] = []
    @Published private(set) var isScanning = false
    @Published private(set) var isBusy = false
    @Published private(set) var isProcessing = false
    @Published private(set) var isFinished = false
    @Published private(set) var status = "Stand in the first room and tap Start."
    private var startedAt = Date.now

    var canFinish: Bool { !isScanning && !isProcessing && !isBusy && !isFinished && (pending != nil || !rooms.isEmpty) }

    override init() {
        captureView = RoomCaptureView(frame: .zero)
        super.init()
        captureView.delegate = self
    }

    // RoomCaptureViewDelegate inherits NSCoding; this object is never archived.
    nonisolated required init?(coder: NSCoder) { nil }
    nonisolated func encode(with coder: NSCoder) {}

    func start() {
        if isFinished || rooms.isEmpty {
            // First room, or a new floor after Finish: the paused ARSession restarts in a new frame.
            rooms = []
            structure = nil
            isFinished = false
            startedAt = .now
        }
        pending = nil
        isProcessing = false
        exported = []
        captureView.captureSession.run(configuration: RoomCaptureSession.Configuration())
        isScanning = true
        status = "Scanning room \(rooms.count + 1). Walk the walls, then tap Stop."
    }

    func stop() {
        // Keep the ARSession running so the next room lands in the same world frame (WWDC23 10192).
        captureView.captureSession.stop(pauseARSession: false)
        isScanning = false
        isProcessing = true
        status = "Processing room \(rooms.count + 1)..."
    }

    func addAnotherRoom() {
        guard let pending else { return }
        rooms.append(pending)
        self.pending = nil
        // Not start(): the user walks to the next room first, with the ARSession still tracking.
        status = "Kept \(rooms.count) room(s). Walk to the next room, then tap Start. Keep the app open."
    }

    func finish() async {
        if let pending {
            rooms.append(pending)
            self.pending = nil
        }
        captureView.captureSession.arSession.pause()
        isBusy = true
        status = "Merging \(rooms.count) room(s)..."
        do {
            structure = try await StructureBuilder(options: [.beautifyObjects]).capturedStructure(from: rooms)
            status = "Merged \(rooms.count) room(s). Tap Export."
        } catch {
            structure = nil
            status = "Merge failed: \(error.localizedDescription) Export still saves each room as room_N.json."
        }
        isBusy = false
        isFinished = true
    }

    func export() {
        let folder = URL.documentsDirectory
            .appending(path: "scan-" + startedAt.formatted(.iso8601).replacingOccurrences(of: ":", with: ""))
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
        var files: [URL] = []
        func write(_ value: some Encodable, _ name: String) throws {
            let url = folder.appending(path: name)
            try encoder.encode(value).write(to: url)
            files.append(url)
        }
        do {
            try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
            // JSON first: a USDZ failure must not cost the scan data.
            if let structure { try write(structure, "structure.json") }
            for (index, room) in rooms.enumerated() { try write(room, "room_\(index).json") }
            try write(ScanNotes(capturedAt: startedAt.formatted(.iso8601), device: Self.deviceModel, rooms: rooms.count),
                      "scan_notes.json")
            if let structure {
                let usdz = folder.appending(path: "structure.usdz")
                try structure.export(to: usdz, exportOptions: .parametric)
                files.append(usdz)
            }
            status = "Saved \(files.count) files to Files > On My iPhone > RoomScanner > \(folder.lastPathComponent)."
        } catch {
            status = "Export stopped after \(files.count) files: \(error.localizedDescription)"
        }
        exported = files
    }

    nonisolated func captureView(shouldPresent roomDataForProcessing: CapturedRoomData, error: (any Error)?) -> Bool {
        true
    }

    nonisolated func captureView(didPresent processedResult: CapturedRoom, error: (any Error)?) {
        let problem = error?.localizedDescription
        Task { @MainActor in self.present(processedResult, problem: problem) }
    }

    private func present(_ room: CapturedRoom, problem: String?) {
        guard !isScanning else { return } // a late result from a scan the user already restarted
        isProcessing = false
        if let problem {
            status = "Room processing failed: \(problem) Tap Start to rescan this room."
            return
        }
        pending = room
        status = "Room \(rooms.count + 1): \(room.walls.count) walls, \(room.doors.count) doors, "
            + "\(room.openings.count) openings. Add another room, Finish, or Start to rescan it."
    }

    /// Hardware id such as "iPhone15,3" plus the OS version, e.g. "iPhone15,3 iOS 18.5".
    static var deviceModel: String {
        var info = utsname()
        uname(&info)
        let machine = withUnsafeBytes(of: info.machine) { String(decoding: $0.prefix { $0 != 0 }, as: UTF8.self) }
        return "\(machine) iOS \(UIDevice.current.systemVersion)"
    }
}

/// scan_notes.json. exit_labels and notes are filled in later on the Mac.
private struct ScanNotes: Encodable {
    let capturedAt: String
    let device: String
    let rooms: Int
    let exitLabels: [String] = []
    let notes = ""
    // Mesh mode only; nil keys are left out, so Rooms exports are unchanged.
    var mode: String?
    var frame: String?
    var triangles: Int?

    enum CodingKeys: String, CodingKey {
        case capturedAt = "captured_at", device, rooms, exitLabels = "exit_labels", notes, mode, frame, triangles
    }
}

/// Raw LiDAR mesh capture (ARKit scene reconstruction) for spaces RoomPlan cannot handle,
/// such as a 14 x 14 m hall. Exports mesh.ply in world coordinates, meters, y up.
@MainActor
final class MeshModel: ObservableObject {
    let arView = ARView(frame: .zero, cameraMode: .ar, automaticallyConfigureSession: false)
    @Published private(set) var isRunning = false
    @Published private(set) var exported: [URL] = []
    @Published private(set) var status = "Tap Start, then walk the space."
    private var elapsed: TimeInterval = 0
    private var lastTick: Date?
    private var startedAt = Date.now
    private let recorder = KeyframeRecorder()

    static var isSupported: Bool { ARWorldTrackingConfiguration.supportsSceneReconstruction(.meshWithClassification) }

    init() {
        // ponytail: built-in mesh wireframe, not colored by class; add a per-class ModelEntity overlay if coverage is hard to read.
        arView.debugOptions.insert(.showSceneUnderstanding)
        arView.session.delegate = recorder
    }

    private var folder: URL {
        URL.documentsDirectory
            .appending(path: "mesh-" + startedAt.formatted(.iso8601).replacingOccurrences(of: ":", with: ""))
    }

    private var configuration: ARWorldTrackingConfiguration {
        let config = ARWorldTrackingConfiguration()
        config.sceneReconstruction = .meshWithClassification
        config.environmentTexturing = .none
        config.planeDetection = [.horizontal, .vertical]
        config.worldAlignment = .gravity
        if ARWorldTrackingConfiguration.supportsFrameSemantics(.sceneDepth) {
            config.frameSemantics.insert(.sceneDepth)
        }
        return config
    }

    private var meshAnchors: [ARMeshAnchor] {
        arView.session.currentFrame?.anchors.compactMap { $0 as? ARMeshAnchor } ?? []
    }

    func toggle() { isRunning ? pause() : run(options: []) }

    func reset() {
        elapsed = 0
        run(options: [.resetTracking, .removeExistingAnchors])
    }

    func pause() {
        guard isRunning else { return }
        tick()
        arView.session.pause()
        isRunning = false
        lastTick = nil
        UIApplication.shared.isIdleTimerDisabled = false
    }

    private func run(options: ARSession.RunOptions) {
        if elapsed == 0 {
            startedAt = .now
            recorder.start(in: folder.appending(path: "keyframes"))
        }
        arView.session.run(configuration, options: options)
        isRunning = true
        lastTick = .now
        exported = []
        // A hall takes several minutes; the screen must not lock mid-scan.
        UIApplication.shared.isIdleTimerDisabled = true
        tick()
    }

    /// Called once a second by the view while running.
    func tick() {
        guard isRunning else { return }
        if let lastTick { elapsed += Date.now.timeIntervalSince(lastTick) }
        lastTick = .now
        let anchors = meshAnchors
        let triangles = anchors.reduce(0) { $0 + $1.geometry.faces.count }
        let floor = anchors.reduce(Float(0)) { $0 + $1.geometry.floorArea }
        let seconds = Int(elapsed)
        status = String(format: "%d anchors, %d triangles, floor %.1f m^2, %d keyframes, %d:%02d",
                        anchors.count, triangles, floor, recorder.count, seconds / 60, seconds % 60)
        if let problem = recorder.lastError { status += "\nKeyframe failed: \(problem)" }
    }

    func export() {
        pause()
        let anchors = meshAnchors
        guard !anchors.isEmpty else {
            status = "No mesh yet. Tap Start and scan first."
            return
        }
        recorder.flush()
        do {
            try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
            let (ply, triangles) = Self.ply(anchors)
            let plyURL = folder.appending(path: "mesh.ply")
            try ply.write(to: plyURL)
            let encoder = JSONEncoder()
            encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
            let notes = ScanNotes(capturedAt: startedAt.formatted(.iso8601), device: ScanModel.deviceModel, rooms: 0,
                                  mode: "mesh", frame: "arkit_world_gravity_y_up", triangles: triangles)
            let notesURL = folder.appending(path: "scan_notes.json")
            try encoder.encode(notes).write(to: notesURL)
            // No keyframes folder when tracking never settled; the mesh alone is still a valid export.
            let keyframes = (try? FileManager.default.contentsOfDirectory(
                at: folder.appending(path: "keyframes"), includingPropertiesForKeys: nil)) ?? []
            // Shared flat: frames.jsonl names files relative to itself, so AirDrop to ~/Downloads still resolves.
            exported = [plyURL, notesURL] + keyframes.sorted { $0.lastPathComponent < $1.lastPathComponent }
            status = "Saved \(triangles) triangles and \(recorder.count) keyframes to Files > On My iPhone > RoomScanner > \(folder.lastPathComponent)."
        } catch {
            status = "Export failed: \(error.localizedDescription)"
        }
    }

    /// Binary little-endian PLY: float xyz per vertex, uchar-counted int indices plus a uchar
    /// ARMeshClassification raw value per face. Vertices stay indexed per anchor as ARKit gives them.
    private static func ply(_ anchors: [ARMeshAnchor]) -> (Data, Int) {
        var vertexData = Data()
        var faceData = Data()
        var vertexCount = 0
        var faceCount = 0
        for anchor in anchors {
            let geometry = anchor.geometry
            for index in 0..<geometry.vertices.count {
                let world = anchor.transform * SIMD4(geometry.vertex(index), 1)
                for value in [world.x, world.y, world.z] { vertexData.appendLittleEndian(value.bitPattern) }
            }
            for face in 0..<geometry.faces.count {
                faceData.append(3)
                for corner in 0..<3 {
                    faceData.appendLittleEndian(Int32(vertexCount + geometry.vertexIndex(face: face, corner: corner)))
                }
                faceData.append(geometry.classification(face: face))
            }
            vertexCount += geometry.vertices.count
            faceCount += geometry.faces.count
        }
        let header = """
            ply
            format binary_little_endian 1.0
            comment rehearsal arkit mesh v1
            element vertex \(vertexCount)
            property float x
            property float y
            property float z
            element face \(faceCount)
            property list uchar int vertex_indices
            property uchar classification
            end_header

            """
        return (Data(header.utf8) + vertexData + faceData, faceCount)
    }
}

/// Saves a keyframe whenever the camera has moved 0.5 m or turned 15 degrees since the last one
/// (at most 2 per second, 400 per scan) so computer vision can run offline without rescanning:
/// kf_<n>.jpg, kf_<n>_depth.png (uint16 millimeters, 0 = no depth) and one frames.jsonl line.
/// ARSession calls it on the main queue; encoding and file writes run on `queue`.
final class KeyframeRecorder: NSObject, ARSessionDelegate, @unchecked Sendable {
    private static let minDistance: Float = 0.5
    private static let minAngle: Float = 15 * .pi / 180
    private static let minInterval: TimeInterval = 0.5
    private static let maxCount = 400
    private static let longSide: CGFloat = 1440

    private let queue = DispatchQueue(label: "org.nova.RoomScanner.keyframes", qos: .utility)
    private let context = CIContext()
    private var folder: URL?
    private var last: (pose: simd_float4x4, time: TimeInterval)?
    // Main queue only.
    private(set) var count = 0
    private(set) var lastError: String?

    func start(in folder: URL) {
        self.folder = folder
        last = nil
        count = 0
        lastError = nil
    }

    /// Blocks until every queued keyframe is on disk.
    func flush() { queue.sync {} }

    func session(_ session: ARSession, didUpdate frame: ARFrame) {
        guard let folder, count < Self.maxCount, case .normal = frame.camera.trackingState else { return }
        let pose = frame.camera.transform
        if let last {
            guard frame.timestamp - last.time >= Self.minInterval else { return }
            let moved = simd_distance(pose.columns.3, last.pose.columns.3) >= Self.minDistance
            guard moved || Self.angle(last.pose, pose) >= Self.minAngle else { return }
        }
        last = (pose, frame.timestamp)
        // ponytail: holds the two pixel buffers, not the ARFrame; 2 per second is far below the camera pool size.
        let keyframe = Keyframe(n: count, time: frame.timestamp, image: frame.capturedImage,
                                depth: frame.sceneDepth?.depthMap, intrinsics: frame.camera.intrinsics, pose: pose)
        count += 1
        queue.async {
            do {
                try self.write(keyframe, to: folder)
            } catch {
                let problem = "kf_\(keyframe.n): \(error.localizedDescription)"
                DispatchQueue.main.async { self.lastError = problem }
            }
        }
    }

    /// Rotation angle between two poses, 0 to pi.
    private static func angle(_ a: simd_float4x4, _ b: simd_float4x4) -> Float {
        let turn = (simd_quatf(a).inverse * simd_quatf(b)).angle
        return min(turn, 2 * .pi - turn)
    }

    private func write(_ keyframe: Keyframe, to folder: URL) throws {
        try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
        let sourceWidth = CVPixelBufferGetWidth(keyframe.image)
        let sourceHeight = CVPixelBufferGetHeight(keyframe.image)
        let scale = Self.longSide / CGFloat(max(sourceWidth, sourceHeight))
        let image = CIImage(cvPixelBuffer: keyframe.image).transformed(by: CGAffineTransform(scaleX: scale, y: scale))
        let options = [kCGImageDestinationLossyCompressionQuality as CIImageRepresentationOption: 0.8]
        guard let jpeg = context.jpegRepresentation(of: image, colorSpace: CGColorSpaceCreateDeviceRGB(), options: options)
        else { throw KeyframeError("JPEG encoding failed") }
        let imageName = "kf_\(keyframe.n).jpg"
        try jpeg.write(to: folder.appending(path: imageName))

        let depthName = keyframe.depth.map { _ in "kf_\(keyframe.n)_depth.png" }
        if let depth = keyframe.depth, let depthName {
            try Self.writeDepthPNG(depth, to: folder.appending(path: depthName))
        }
        let k = keyframe.intrinsics
        let s = Float(scale)
        let line: [String: Any] = [
            "n": keyframe.n,
            "t": keyframe.time,
            "image": imageName,
            "depth": depthName ?? NSNull(),
            "width": Int((CGFloat(sourceWidth) * scale).rounded()),
            "height": Int((CGFloat(sourceHeight) * scale).rounded()),
            "depth_width": keyframe.depth.map(CVPixelBufferGetWidth) ?? NSNull(),
            "depth_height": keyframe.depth.map(CVPixelBufferGetHeight) ?? NSNull(),
            // ARKit intrinsics are for capturedImage; a uniform resize scales them by the same factor.
            "intrinsics": [k[0][0] * s, k[1][1] * s, k[2][0] * s, k[2][1] * s].map(Double.init),
            "camera_to_world": (0..<4).flatMap { row in (0..<4).map { column in Double(keyframe.pose[column][row]) } },
        ]
        var data = try JSONSerialization.data(withJSONObject: line, options: [.sortedKeys])
        data.append(0x0A)
        let jsonl = folder.appending(path: "frames.jsonl")
        if !FileManager.default.fileExists(atPath: jsonl.path) {
            FileManager.default.createFile(atPath: jsonl.path, contents: nil)
        }
        let handle = try FileHandle(forWritingTo: jsonl)
        defer { try? handle.close() }
        try handle.seekToEnd()
        try handle.write(contentsOf: data)
    }

    /// Float32 meters to a 16-bit grayscale PNG in millimeters (big-endian samples, as PNG stores them).
    private static func writeDepthPNG(_ depth: CVPixelBuffer, to url: URL) throws {
        guard CVPixelBufferGetPixelFormatType(depth) == kCVPixelFormatType_DepthFloat32 else {
            throw KeyframeError("unexpected depth format")
        }
        CVPixelBufferLockBaseAddress(depth, .readOnly)
        defer { CVPixelBufferUnlockBaseAddress(depth, .readOnly) }
        guard let base = CVPixelBufferGetBaseAddress(depth) else { throw KeyframeError("no depth data") }
        let width = CVPixelBufferGetWidth(depth)
        let height = CVPixelBufferGetHeight(depth)
        let rowBytes = CVPixelBufferGetBytesPerRow(depth)
        let millimeters = (0..<height).flatMap { y in
            let row = base.advanced(by: y * rowBytes).assumingMemoryBound(to: Float32.self)
            return (0..<width).map { x in
                let meters = row[x]
                return (meters.isFinite && meters > 0 ? UInt16(min(meters * 1000, 65535).rounded()) : 0).bigEndian
            }
        }
        guard let provider = CGDataProvider(data: millimeters.withUnsafeBytes { Data($0) } as CFData),
              let image = CGImage(width: width, height: height, bitsPerComponent: 16, bitsPerPixel: 16,
                                  bytesPerRow: width * 2, space: CGColorSpaceCreateDeviceGray(),
                                  bitmapInfo: CGBitmapInfo(rawValue: CGImageAlphaInfo.none.rawValue),
                                  provider: provider, decode: nil, shouldInterpolate: false, intent: .defaultIntent),
              let destination = CGImageDestinationCreateWithURL(url as CFURL, UTType.png.identifier as CFString, 1, nil)
        else { throw KeyframeError("depth PNG setup failed") }
        CGImageDestinationAddImage(destination, image, nil)
        guard CGImageDestinationFinalize(destination) else { throw KeyframeError("depth PNG write failed") }
    }
}

/// The parts of an ARFrame a keyframe needs; pixel buffers are only read on the writer queue.
private struct Keyframe: @unchecked Sendable {
    let n: Int
    let time: TimeInterval
    let image: CVPixelBuffer
    let depth: CVPixelBuffer?
    let intrinsics: simd_float3x3
    let pose: simd_float4x4
}

private struct KeyframeError: LocalizedError {
    let errorDescription: String?
    init(_ message: String) { errorDescription = message }
}

private extension ARMeshGeometry {
    /// Vertex in anchor-local coordinates (packed float3).
    func vertex(_ index: Int) -> SIMD3<Float> {
        let pointer = vertices.buffer.contents()
            .advanced(by: vertices.offset + vertices.stride * index)
            .assumingMemoryBound(to: Float.self)
        return SIMD3(pointer[0], pointer[1], pointer[2])
    }

    func vertexIndex(face: Int, corner: Int) -> Int {
        let pointer = faces.buffer.contents()
            .advanced(by: (face * faces.indexCountPerPrimitive + corner) * faces.bytesPerIndex)
        return faces.bytesPerIndex == 2
            ? Int(pointer.assumingMemoryBound(to: UInt16.self).pointee)
            : Int(pointer.assumingMemoryBound(to: UInt32.self).pointee)
    }

    /// ARMeshClassification raw value, 0 (none) when the session has no classification.
    func classification(face: Int) -> UInt8 {
        guard let classification else { return 0 }
        return classification.buffer.contents()
            .advanced(by: classification.offset + classification.stride * face)
            .assumingMemoryBound(to: UInt8.self).pointee
    }

    var floorArea: Float {
        (0..<faces.count).reduce(Float(0)) { area, face in
            guard classification(face: face) == UInt8(ARMeshClassification.floor.rawValue) else { return area }
            let a = vertex(vertexIndex(face: face, corner: 0))
            let b = vertex(vertexIndex(face: face, corner: 1))
            let c = vertex(vertexIndex(face: face, corner: 2))
            return area + simd_length(simd_cross(b - a, c - a)) / 2
        }
    }
}

private extension Data {
    mutating func appendLittleEndian<T: FixedWidthInteger>(_ value: T) {
        Swift.withUnsafeBytes(of: value.littleEndian) { append(contentsOf: $0) }
    }
}
