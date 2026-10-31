# Security policy

NexusAndroid drives a real phone, so safety issues matter more than usual: a way to make an agent pay, send,
publish, delete for good or do any other action of the shared confirmation policy without `confirm=true`, to type into a password field, to act on a locked phone or in a protected
app, or to read a security code that should be masked.

Please report such issues privately through
[GitHub private vulnerability reporting](https://github.com/pironjulien/NexusAndroid/security/advisories/new),
not in a public issue. Include the tool call, the screen or app involved and what happened. You will get an answer
within a few days.

## Scope and limits

- **Shared daemon token** (`nexus-android --http`): the bearer token keeps a misconfigured client or a stray agent
  session from driving the phone through the daemon. It is not a defence against malicious software running on the
  same PC under your account: such software can read the token file, and it can talk to the phone directly through
  the local ADB server (port 5037), which has no authentication.
- **Phone-side read helper** (`nexus-query.dex`, started under the shell identity): it is read-only, accepts
  connections only from the shell or root identity (peer credentials checked by the kernel, which no app on the
  phone can have), and each launch draws a random token that is part of its socket name and must come back in every
  answer, so the PC never trusts another server listening on that name. The repository holds its source only
  (`phone/NexusQuery.java`): every engine build runs `phone/build.ps1`, which compiles it with Google's d8, pinned
  by SHA-256, and embeds the result, so users still need no Java toolchain.
- **Hotspot / `adb tcpip 5555` mode**: classic ADB over TCP is not encrypted (only Android's wireless debugging
  uses TLS). Use it only on a network you control, and turn it off with `adb usb` or a reboot.

## Disclosure

You will get a first answer within 3 days. A confirmed vulnerability is fixed in a new release, usually within 30
days, then disclosed in a GitHub security advisory that credits the reporter unless they prefer otherwise. Please
keep it private until that release is out.

Only the latest release is supported.
