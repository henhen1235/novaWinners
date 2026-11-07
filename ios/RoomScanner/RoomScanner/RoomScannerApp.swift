import RealityKit
import RoomPlan
import SwiftUI

@main
struct RoomScannerApp: App {
    var body: some SwiftUI.Scene {
        WindowGroup { RootView() }
    }
}

private enum CaptureMode: String, CaseIterable {
    case rooms = "Rooms", mesh = "Mesh"
}

/// Rooms is the RoomPlan flow (home-size rooms); Mesh is raw LiDAR for large open spaces.
/// Switching modes discards the other mode's unsaved scan.
private struct RootView: View {
    @State private var mode = CaptureMode.mesh

    var body: some View {
        Group {
            switch mode {
            case .rooms:
                // RoomCaptureView needs a LiDAR device; creating one elsewhere fails.
                if RoomCaptureSession.isSupported {
                    ContentView()
                } else {
                    Text("RoomPlan needs an iPhone or iPad with a LiDAR scanner (Pro models).").padding()
                }
            case .mesh:
                if MeshModel.isSupported {
                    MeshView()
                } else {
                    Text("Mesh capture needs an iPhone or iPad with a LiDAR scanner (Pro models).").padding()
                }
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .safeAreaInset(edge: .top) {
            Picker("Mode", selection: $mode) {
                ForEach(CaptureMode.allCases, id: \.self) { Text($0.rawValue) }
            }
            .pickerStyle(.segmented)
            .padding(8)
            .background(.ultraThinMaterial, in: RoundedRectangle(cornerRadius: 12))
            .padding(.horizontal)
        }
    }
}

private struct MeshView: View {
    @StateObject private var mesh = MeshModel()

    var body: some View {
        ZStack(alignment: .bottom) {
            ARViewContainer(view: mesh.arView).ignoresSafeArea()
            VStack(spacing: 10) {
                Text("Walk slowly, sweep floor to ceiling, go through every doorway")
                    .font(.footnote)
                    .multilineTextAlignment(.center)
                Text(mesh.status)
                    .font(.callout)
                    .monospacedDigit()
                    .multilineTextAlignment(.center)
                HStack {
                    Button(mesh.isRunning ? "Pause" : "Start", action: mesh.toggle)
                    Button("Reset", action: mesh.reset)
                    Button("Export", action: mesh.export)
                }
                if !mesh.exported.isEmpty {
                    ShareLink("Share \(mesh.exported.count) files", items: mesh.exported)
                }
            }
            .buttonStyle(.borderedProminent)
            .padding()
            .background(.ultraThinMaterial, in: RoundedRectangle(cornerRadius: 16))
            .padding()
        }
        .task {
            while !Task.isCancelled {
                try? await Task.sleep(for: .seconds(1))
                mesh.tick()
            }
        }
        .onDisappear(perform: mesh.pause)
    }
}

private struct ARViewContainer: UIViewRepresentable {
    let view: ARView
    func makeUIView(context: Context) -> ARView { view }
    func updateUIView(_ uiView: ARView, context: Context) {}
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
