// swift-tools-version:5.9
import PackageDescription

let package = Package(
    name: "CodeReel",
    platforms: [.macOS(.v14)],
    targets: [
        .executableTarget(name: "CodeReel", path: "Sources/CodeReel")
    ]
)
