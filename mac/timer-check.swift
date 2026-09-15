// Why the recording timer read 0.0s, proven without touching the microphone.
//
//   swift mac/timer-check.swift
//
// Recorder.start() awaits AVCaptureDevice.requestAccess, which resumes on an
// arbitrary thread. Timer.scheduledTimer installs itself on *the calling thread's*
// run loop — a cooperative thread has none that is running, so the timer is created
// and never fires. RunLoop.main.add(_:forMode:.common) targets the main run loop
// explicitly, which is the fix in Recorder.start().

import Foundation

final class Counter: @unchecked Sendable {
    private let lock = NSLock()
    private var n = 0
    func bump() { lock.lock(); n += 1; lock.unlock() }
    var value: Int { lock.lock(); defer { lock.unlock() }; return n }
}

let scheduledOffMain = Counter()   // the old code
let addedToMainLoop = Counter()    // the fix

// Both timers are created from a detached (non-main) task, exactly as they were
// after `await AVCaptureDevice.requestAccess(for: .audio)`.
Task.detached {
    precondition(!Thread.isMainThread, "this check must schedule from a background thread")

    _ = Timer.scheduledTimer(withTimeInterval: 0.05, repeats: true) { _ in
        scheduledOffMain.bump()
    }

    let tick = Timer(timeInterval: 0.05, repeats: true) { _ in
        addedToMainLoop.bump()
    }
    RunLoop.main.add(tick, forMode: .common)
}

RunLoop.main.run(until: Date().addingTimeInterval(1.0))

print("Timer.scheduledTimer from a background thread : \(scheduledOffMain.value) ticks in 1.0s")
print("RunLoop.main.add from a background thread     : \(addedToMainLoop.value) ticks in 1.0s")

precondition(scheduledOffMain.value == 0,
             "expected the old scheduling to never fire — the bug would not reproduce")
precondition(addedToMainLoop.value > 10,
             "expected ~20 ticks from the main run loop, got \(addedToMainLoop.value)")
print("OK — the elapsed counter ticks only with the RunLoop.main.add scheduling.")
