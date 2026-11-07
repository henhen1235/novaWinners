import RoomPlan
import SwiftUI

@main
struct RoomScannerApp: App {
    var body: some Scene {
        WindowGroup {
            // RoomCaptureView needs a LiDAR device; creating one elsewhere fails.
            if RoomCaptureSession.isSupported {
                ContentView()
            } else {
                Text("RoomPlan needs an iPhone or iPad with a LiDAR scanner (Pro models).")
                    .padding()
            }
        }
    }
}

struct ContentView: View {
    @StateObject private var scan = ScanModel()

    var body: some View {
        ZStack(alignment: .bottom) {
            CaptureView(view: scan.captureView).ignoresSafeArea()
            VStack(spacing: 10) {
                Text(scan.status)
                    .font(.callout)
                    .multilineTextAlignment(.center)
                HStack {
                    Button("Start", action: scan.start).disabled(scan.isScanning || scan.isBusy)
                    Button("Stop", action: scan.stop).disabled(!scan.isScanning)
                }
                HStack {
                    Button("Add another room", action: scan.addAnotherRoom).disabled(scan.pending == nil || scan.isScanning)
                    Button("Finish") { Task { await scan.finish() } }.disabled(!scan.canFinish)
                }
                if scan.exported.isEmpty {
                    Button("Export", action: scan.export).disabled(!scan.isFinished)
                } else {
                    ShareLink("Share \(scan.exported.count) files", items: scan.exported)
                }
            }
            .buttonStyle(.borderedProminent)
            .padding()
            .background(.ultraThinMaterial, in: RoundedRectangle(cornerRadius: 16))
            .padding()
        }
    }
}

private struct CaptureView: UIViewRepresentable {
    let view: RoomCaptureView
    func makeUIView(context: Context) -> RoomCaptureView { view }
    func updateUIView(_ uiView: RoomCaptureView, context: Context) {}
}
