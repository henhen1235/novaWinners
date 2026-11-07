import ARKit
import RealityKit
import RoomPlan
import SwiftUI

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

    static var isSupported: Bool { ARWorldTrackingConfiguration.supportsSceneReconstruction(.meshWithClassification) }

    init() {
        // ponytail: built-in mesh wireframe, not colored by class; add a per-class ModelEntity overlay if coverage is hard to read.
        arView.debugOptions.insert(.showSceneUnderstanding)
    }

    private var configuration: ARWorldTrackingConfiguration {
        let config = ARWorldTrackingConfiguration()
        config.sceneReconstruction = .meshWithClassification
        config.environmentTexturing = .none
        config.planeDetection = [.horizontal, .vertical]
        config.worldAlignment = .gravity
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
        if elapsed == 0 { startedAt = .now }
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
        status = String(format: "%d anchors, %d triangles, floor %.1f m^2, %d:%02d",
                        anchors.count, triangles, floor, seconds / 60, seconds % 60)
    }

    func export() {
        pause()
        let anchors = meshAnchors
        guard !anchors.isEmpty else {
            status = "No mesh yet. Tap Start and scan first."
            return
        }
        let folder = URL.documentsDirectory
            .appending(path: "mesh-" + startedAt.formatted(.iso8601).replacingOccurrences(of: ":", with: ""))
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
            exported = [plyURL, notesURL]
            status = "Saved \(triangles) triangles to Files > On My iPhone > RoomScanner > \(folder.lastPathComponent)."
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
