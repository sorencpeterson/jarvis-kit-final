// Creates the "Coach Output" multi-output device: (AirPods if connected, else
// MacBook Pro Speakers) + BlackHole 2ch. User-space CoreAudio — no admin needed.
// Idempotent: exits if Coach Output already exists.
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
func str(_ id: AudioDeviceID, _ sel: AudioObjectPropertySelector) -> String {
    var addr = AudioObjectPropertyAddress(mSelector: sel,
        mScope: kAudioObjectPropertyScopeGlobal, mElement: kAudioObjectPropertyElementMain)
    var size = UInt32(MemoryLayout<CFString?>.size)
    var out: CFString? = nil
    let err = withUnsafeMutablePointer(to: &out) {
        AudioObjectGetPropertyData(id, &addr, 0, nil, &size, $0)
    }
    return err == noErr ? (out as String? ?? "") : ""
}
let all = devices()
var uidBlack = "", uidHear = "", nameHear = ""
var airpods: (String, String)? = nil
for d in all {
    let name = str(d, kAudioObjectPropertyName)
    let uid = str(d, kAudioDevicePropertyDeviceUID)
    if name == "Coach Output" { print("Coach Output already exists — nothing to do"); exit(0) }
    if name.contains("BlackHole 2ch") { uidBlack = uid }
    if name.contains("AirPods") { airpods = (uid, name) }
    if name.contains("MacBook Pro Speakers") { uidHear = uid; nameHear = name }
}
if let ap = airpods { uidHear = ap.0; nameHear = ap.1 }
guard !uidBlack.isEmpty else { print("ERROR: BlackHole 2ch not found — is the driver installed? (restart coreaudiod or log out/in)"); exit(1) }
guard !uidHear.isEmpty else { print("ERROR: no listening device found"); exit(1) }
let desc: [String: Any] = [
    kAudioAggregateDeviceNameKey as String: "Coach Output",
    kAudioAggregateDeviceUIDKey as String: "com.secondbrain.coachoutput",
    kAudioAggregateDeviceIsStackedKey as String: 1,
    kAudioAggregateDeviceMainSubDeviceKey as String: uidHear,
    kAudioAggregateDeviceSubDeviceListKey as String: [
        [kAudioSubDeviceUIDKey as String: uidHear],
        [kAudioSubDeviceUIDKey as String: uidBlack,
         kAudioSubDeviceDriftCompensationKey as String: 1],
    ],
]
var aggID = AudioDeviceID(0)
let status = AudioHardwareCreateAggregateDevice(desc as CFDictionary, &aggID)
if status == noErr {
    print("CREATED: 'Coach Output' = \(nameHear) + BlackHole 2ch  (id \(aggID))")
    print("Select it as sound OUTPUT during calls (menu bar volume icon).")
} else {
    print("ERROR creating aggregate: \(status)")
    exit(1)
}
