# NexusAndroid

**Your Android phone for Antigravity, over plain ADB.** The agent sees the screen and its UI tree, taps, types with
accents and emoji, scrolls, opens apps and links, reads SMS/RCS and notifications, prepares replies, moves files and
changes quick settings. No root, no app to install on the phone, no cloud relay: a USB cable or the local Wi-Fi.

- **Text first**: numbered elements with their role and text, an image only when the screen has nothing readable.
- **Verified gestures**: every step reports whether its effect was read back.
- **Safe by default**: sending, calling, paying or publishing needs your explicit yes for that exact action.

## Requirements

Windows 10 or 11 and an Android phone with USB debugging. The plugin talks to the NexusAndroid service installed on
the PC (`127.0.0.1`, authenticated by the local token of `%USERPROFILE%\.nexus\mcp-token.txt`). If it is missing,
ask Antigravity to install NexusAndroid (its skill gives the steps), or follow
[installation](https://github.com/pironjulien/NexusAndroid#readme): no administrator rights.

## Privacy

Everything runs on your PC and talks to your phone over ADB: no account, no telemetry, no cloud relay. Tool results
go to Antigravity, which may send them to Google under its own privacy policy; NexusAndroid sends nothing to the
author or to any third party. Details: [privacy](https://github.com/pironjulien/NexusAndroid#privacy).

## Links

- Source and releases: [github.com/pironjulien/NexusAndroid](https://github.com/pironjulien/NexusAndroid) (MIT)
- Project page: [julienpiron.fr/nexusandroid](https://julienpiron.fr/nexusandroid/)
- Support: [GitHub issues](https://github.com/pironjulien/NexusAndroid/issues)
