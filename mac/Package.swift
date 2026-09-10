// swift-tools-version:5.9
import PackageDescription

let package = Package(
    name: "VoxDemo",
    platforms: [.macOS(.v14)],
    targets: [
        .executableTarget(name: "VoxDemo", path: "Sources/VoxDemo")
    ]
)
