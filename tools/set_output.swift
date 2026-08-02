// set_output <device name substring> — switches the system default output device.
// set_output --get  — prints the current default output device name.
import CoreAudio
import Foundation

func devices() -> [AudioDeviceID] {
    var addr = AudioObjectPropertyAddress(mSelector: kAudioHardwarePropertyDevices,
        mScope: kAudioObjectPropertyScopeGlobal, mElement: kAudioObjectPropertyElementMain)
    var size: UInt32 = 0
    AudioObjectGetPropertyDataSize(AudioObjectID(kAudioObjectSystemObject), &addr, 0, nil, &size)
    var ids = [AudioDeviceID](repeating: 0, count: Int(size) / MemoryLayout<AudioDeviceID>.size)
    AudioObjectGetPropertyData(AudioObjectID(kAudioObjectSystemObject), &addr, 0, nil, &size, &ids)
    return ids
}
func name(_ id: AudioDeviceID) -> String {
    var addr = AudioObjectPropertyAddress(mSelector: kAudioObjectPropertyName,
        mScope: kAudioObjectPropertyScopeGlobal, mElement: kAudioObjectPropertyElementMain)
    var size = UInt32(MemoryLayout<CFString?>.size)
    var out: CFString? = nil
    let err = withUnsafeMutablePointer(to: &out) { AudioObjectGetPropertyData(id, &addr, 0, nil, &size, $0) }
    return err == noErr ? (out as String? ?? "") : ""
}
var defAddr = AudioObjectPropertyAddress(mSelector: kAudioHardwarePropertyDefaultOutputDevice,
    mScope: kAudioObjectPropertyScopeGlobal, mElement: kAudioObjectPropertyElementMain)
let args = CommandLine.arguments
if args.count > 1 && args[1] == "--get" {
    var cur = AudioDeviceID(0); var sz = UInt32(MemoryLayout<AudioDeviceID>.size)
    AudioObjectGetPropertyData(AudioObjectID(kAudioObjectSystemObject), &defAddr, 0, nil, &sz, &cur)
    print(name(cur)); exit(0)
}
guard args.count > 1 else { print("usage: set_output <name substring> | --get"); exit(1) }
let want = args[1].lowercased()
for d in devices() where name(d).lowercased().contains(want) {
    var dd = d
    let st = AudioObjectSetPropertyData(AudioObjectID(kAudioObjectSystemObject), &defAddr, 0, nil,
        UInt32(MemoryLayout<AudioDeviceID>.size), &dd)
    print(st == noErr ? "OUTPUT -> \(name(d))" : "ERROR \(st)")
    exit(st == noErr ? 0 : 1)
}
print("device not found: \(args[1])"); exit(1)
