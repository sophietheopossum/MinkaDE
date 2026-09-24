# IndexedDB external-file write holds a QuotaManager mutex across a CondVar wait; localStorage and other quota users deadlock

**Component:** Core :: Storage: Quota Manager
**Version:** Firefox 157.0a1, BuildID 20260901150209 (repackaged as `hellfire-browser-bin 157.0a1-1`; libxul GNU build-id `20aaa76ca9491fc3f640714b599c33ec5a705665`)
**Platform:** Linux x86_64 (kernel 7.2), Wayland
**Frequency:** captured twice on one profile — 62 minutes after startup, and ~24 hours after startup. Only a browser restart recovers.

## Summary

An IndexedDB `put` whose value is stored as an external file enters QuotaManager through `dom/quota/FileStreams.cpp`, takes a mutex in an RAII guard's constructor, and — still inside that constructor, still holding it — blocks in an untimed `ConditionVariableImpl::wait` that never returns. The PBackground thread and the QuotaManager IO thread both block on that mutex, so every quota-tracked storage operation stops.

User-visible: localStorage snapshot requests are never answered, so any content process that touches localStorage blocks forever in the synchronous `PBackgroundLSDatabase` snapshot IPC. On this profile that includes the WebExtensions process, and 15 installed extensions hold `webRequestBlocking` on `<all_urls>`, so every HTTP request in every tab waits on a listener that can never reply. The symptom is therefore "pages have become very slow to load", then individual tabs freezing. The browser chrome stays responsive (the parent's main thread is idle in `poll`); the parent as a whole uses ~2 jiffies of CPU per 10 s while wedged.

## The lock is held across the wait (read from the code)

libxul function at offset `0x76feba0` (0xB0 bytes) is a lock guard's constructor:

- `+0x3c`: `call *[rip]` through GOT slot `0xacb1fa8`, which the dynamic relocations name `_ZN7mozilla6detail9MutexImpl4lockEv` (`MutexImpl::lock()`). The function contains no unlock; release happens in the destructor.
- `+0x46`: if a byte at `+0xc8` of the guarded object is zero, it builds a small descriptor from that object and, **still holding the lock**, calls `0x76f9040` (`+0xa0`), which reaches `ConditionVariableImpl::wait(MutexImpl&)` via `0x76f9260`.

The stuck IndexedDB thread is inside that call. On the second capture, the PBackground thread is blocked at `+0x3c` — the lock call — of this same function.

## Proof of the lock owner (both captures)

Read live from `/proc`, without gdb. A thread blocked in `pthread_mutex_lock` sits in `futex(uaddr, …)` where `uaddr == &mutex.__data.__lock`; glibc keeps `__data.__owner` 8 bytes further in (set on lock, cleared on unlock).

```
mutex 0x7fe705ef7fa8   __lock=2 (contended)  __nusers=1  __owner=2698345
  waiter: "IPDL Background"
  waiter: "QuotaManager IO"
  owner : "IndexedDB IO #3741"   (tid 2698345)

The owner is itself waiting on futex 0x7fe63d0871e0 (__owner=0: a CondVar), in an
untimed pthread_cond_wait. Its lifetime CPU: 1 jiffy.
```

Second capture, same method, same result:

```
mutex 0x7fba7a78bba8   __lock=2 (contended)  __owner=36800
  waiter: "IPDL Background"
  waiter: "QuotaManager IO"
  owner : "IndexedDB IO #147"   (tid 36800; stack in the attached capture)

The owner is itself waiting on futex 0x7fb9ec2c6e60 (__owner=0: a CondVar). Its lifetime
CPU: 1 jiffy.
```

## Stacks

Symbols recovered from a stripped libxul (`.dynsym` has 2 symbols; no `.symtab` or debuglink): exact function bounds from `.eh_frame_hdr`'s search table (266,214 FDEs), identities from the string literals each function and its callees reference. Offsets are libxul-relative; caller frames are return address − 1, as eu-stack prints them.

### Second capture (the one attached)

**Holder — `IndexedDB IO #147`**, untimed `ConditionVariableImpl::wait`:
```
0x76f9290   wait site (fn 0x76f9260)
0x76f918d   fn 0x76f9040 — region referencing "FlagOriginInfoAsDirtyOnDisk: storage not initialized", "QuotaManager"
0x76fec44   fn 0x76feba0 +0xa4 — the lock guard's constructor above
0x76fe87f   fn 0x76fe830
0x41cf0a2   ./../../../dom/quota/FileStreams.cpp
0x41cf714   ./../../../dom/quota/FileStreams.cpp
0x418768e   ObjectStoreAddOrPutRequestOp::DoDatabaseWork   (dom/indexedDB/ActorsParent.cpp)
0x383ac9d … IndexedDB operation dispatch / thread loop
```

**Waiter — `IPDL Background`**, `MutexImpl::lock`:
```
0x76febe1   fn 0x76feba0 +0x41 — the lock call in the same guard constructor
0x76fe87f   fn 0x76fe830 — same caller the holder came through
0x7b157f4   localStorage Connection code
0x7b14be5   RecvAsyncFinish
0x7b276ae   PBackgroundLSSnapshot message dispatch
0x7b139d2   PBackgroundLSDatabase message dispatch
```

**Waiter — `QuotaManager IO`**, `MutexImpl::lock`, identical to the first capture:
```
0x76f8f0f
0x76f9645
0x771af43   SaveOriginAccessTimeOp::DoDirectoryWork   (dom/quota/OriginOperations.cpp)
0x49d1c2e   dom/quota/OriginOperationBase.cpp (promise-chaining lambda)
0x4556a7f   MozPromise ThenValue / ResolveOrRejectRunnable plumbing
```

### First capture

Holder `IndexedDB IO #3741`: the same top four frames (`0x76f9290`, `0x76f918d`, `0x76fec44`, `0x76fe87f`), then `0x418be73` in `dom/quota/FileStreams.cpp`, then `ObjectStoreAddOrPutRequestOp::DoDatabaseWork` (`0x4187800`). The same put entered via a different FileStreams function.

`IPDL Background`: `MutexImpl::lock` from `0x4555280` ← `quota::DirectoryLockImpl::AcquireInternal` (`0x4554d96`) ← `Acquire` (`0x455329e`) ← `OpenClientDirectoryImpl` (`0x4550c24`).

`QuotaManager IO`: as in the second capture.

### Content side (both captures)

Blocked main threads (WebExtensions and page processes) are in `ConditionVariableImpl::wait_for` at libxul `+0x4f28e3b` ← `+0x7b1cb65` ← `+0x7b1c883`, identified by its logging string as `PBackgroundLSDatabase::Msg_PBackgroundLSSnapshotConstructor`. Content processes that did not touch localStorage stay healthy in `g_main_context_iteration`.

## Trigger observed on the second capture

At the exact second of the wedge (the last write to `storage/storage.sqlite`), the Consent-O-Matic extension (`gdpr@cavi.au.dk`) was storing a value in its `browser.storage.local` IndexedDB database (`3647222921wleabcEoxlt-eengsairo`) as an external file:

- `…eengsairo.files/19067` — **0 bytes**, created at the wedge second
- `…eengsairo.files/journals/19067` — its journal, same second; the **only** orphaned IndexedDB file journal in the profile
- `…eengsairo.files/19066` — 154,265 bytes, written successfully one hour earlier (2.5 minutes after browser start)

That matches the holder stack: an `ObjectStoreAddOrPutRequestOp` blocked in the quota file-stream path before writing anything. The lock owner on this capture is that IndexedDB thread; its database is not read directly from the thread, but this is the only external-file write left unfinished anywhere in the profile.

## What the wait is for — open

Not established from the binary. Both threads that could plausibly be expected to signal it — PBackground (for example if this is the eviction path, where origins for eviction are collected on the owning thread) and QuotaManager IO — are themselves blocked on the mutex the waiter holds, which would make this a cycle rather than a lost wakeup. With symbols this should be quick to confirm.

Environment that may matter for an eviction path: the profile lives on a 9.8 GB partition with 2.7 GB free; `storage/default` holds 2.3 GB and `storage/permanent` 1.6 GB across 1,944 origins; no quota prefs are overridden.

## Reproduction

Not reproduced on demand. Observed with long-running sessions and many origins; the second occurrence followed an extension writing a ~150 KB value to `browser.storage.local`, which the same extension had written successfully an hour earlier — so the write is necessary but not sufficient.

## Attachments

- `parent-stacks-24-9.txt` — `eu-stack -b -m` of every parent thread at the second capture
- `find-lock-owner.py` — reads the contended mutex's owner from `/proc` (root required; the parent is non-dumpable)
- `fde.py`, `resolve.py` — the offset-to-source resolution for a stripped libxul
