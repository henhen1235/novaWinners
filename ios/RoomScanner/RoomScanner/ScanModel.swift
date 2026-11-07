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
    private static var deviceModel: String {
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

    enum CodingKeys: String, CodingKey {
        case capturedAt = "captured_at", device, rooms, exitLabels = "exit_labels", notes
    }
}
