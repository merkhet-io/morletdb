# WinCC OA JavaScript Manager API used by the adapter

**Status:** experimental WinCC OA adapter, awaiting validation in WinCC OA. Nothing here has run in WinCC OA yet.

Every function, type and attribute of WinCC OA that the adapter (`src/`, `index.js`, `bootstrap.js`) uses is listed
here, with the official documentation page it was taken from (read 2026-11-14). The API reference is for
`winccoa-manager` **2.6.6**, the version the WinCC OA 3.21 documentation links; which API version each 3.21 patch
installs is not stated (docs/winccoa.md section 8, question 6). The test fake (`test/fake-winccoa.js`) implements
exactly the rows of the first two tables and nothing more; `test/api-surface.test.js` checks that, and that the
adapter calls nothing else.

No WinCC OA file is part of this repository: `winccoa-manager` is loaded at run time inside the JavaScript Manager
(`require('winccoa-manager')` in `index.js` and `bootstrap.js` only), and no type definitions were copied.

Base URLs: `API` = https://www.winccoa.com/documentation/WinCCOA/latest/en_US/apis/winccoa-manager/2.6.6/,
`DOC` = https://www.winccoa.com/documentation/WinCCOA/latest/en_US/.

## WinccoaManager methods

| Method | Signature as documented | Used for | Source | Status |
|---|---|---|---|---|
| `constructor` | `new WinccoaManager(options?: Partial<Omit<WinccoaOptions, "userId">>)` | one instance per script | API `classes/WinccoaManager.html#constructor` | confirmed |
| `dpConnect` | `dpConnect(callback: WinccoaDpConnectCallback, dpeNames: string \| string[], answer?: boolean): number` | asset subscriptions (mode `connect`); slot inputs | API `classes/WinccoaManager.html#dpconnect` | confirmed for element names; for attribute addresses see below |
| `dpDisconnect` | `dpDisconnect(id: number): number` | shutdown | API `classes/WinccoaManager.html#dpdisconnect` | confirmed |
| `dpQueryConnectSingle` | `dpQueryConnectSingle(callback: WinccoaDpQueryConnectCallback, answer: boolean, query: string, blockingTime?: number): number` | asset subscriptions (mode `query`) | API `classes/WinccoaManager.html#dpqueryconnectsingle` | confirmed; the query form used is unconfirmed (below) |
| `dpQueryDisconnect` | `dpQueryDisconnect(id: number): number` | shutdown, query fallback | API `classes/WinccoaManager.html#dpquerydisconnect` | confirmed |
| `dpSetWait` | `dpSetWait(dpeNames: string \| string[], values: unknown): Promise<boolean>` | status and results, namespace only | API `classes/WinccoaManager.html#dpsetwait` | confirmed |
| `dpElementType` | `dpElementType(dpeName: string): WinccoaElementType` | value type of an asset element | API `classes/WinccoaManager.html#dpelementtype` | confirmed |
| `dpExists` | `dpExists(dpeName: string): boolean` | bootstrap | API `classes/WinccoaManager.html#dpexists` | confirmed; "true if at least one part of a data point identifier can be resolved", so the bootstrap also checks `dpTypeName` |
| `dpCreate` | `dpCreate(dpeName: string, dpType: string, systemId?: number, dpId?: number): Promise<boolean>` | bootstrap | API `classes/WinccoaManager.html#dpcreate` | confirmed |
| `dpTypeCreate` | `dpTypeCreate(startNode: WinccoaDpTypeNode): Promise<boolean>` | bootstrap | API `classes/WinccoaManager.html#dptypecreate` | confirmed |
| `dpTypeGet` | `dpTypeGet(dpt: string, includeSubTypes?: boolean): WinccoaDpTypeNode` | bootstrap (compare structure) | API `classes/WinccoaManager.html#dptypeget` | confirmed |
| `dpTypes` | `dpTypes(pattern?: string, systemId?: number, includeEmpty?: boolean): string[]` | bootstrap | API `classes/WinccoaManager.html#dptypes` | confirmed |
| `dpTypeName` | `dpTypeName(dp: string): string` | bootstrap | API `classes/WinccoaManager.html#dptypename` | confirmed |
| `setOptions` | `setOptions(options: Partial<Omit<WinccoaOptions, "userId">>): boolean` | `longAsBigInt: true`, `timeFormat` | API `classes/WinccoaManager.html#setoptions` | confirmed |
| `getVersionInfo` | `getVersionInfo(): WinccoaVersionDetails` | version in the status datapoint and log | API `classes/WinccoaManager.html#getversioninfo` | confirmed |
| `registerExitCallback` | `registerExitCallback(callback: () => Promise<void>): void` | clean shutdown (PMON exit signal or `exit`) | API `classes/WinccoaManager.html#registerexitcallback` | confirmed |
| `exit` | `exit(exitCode?: number): void` | bootstrap end; unusable configuration | API `classes/WinccoaManager.html#exit` | confirmed |
| `logInfo` | `logInfo(...args: unknown[]): void` | log | API `classes/WinccoaManager.html#loginfo` | confirmed |
| `logWarning` | `logWarning(...args: unknown[]): void` | log | API `classes/WinccoaManager.html#logwarning` | confirmed |
| `logSevere` | `logSevere(...args: unknown[]): void` | log | API `classes/WinccoaManager.html#logsevere` | confirmed |

## Module exports

| Export | As documented | Used for | Source | Status |
|---|---|---|---|---|
| `WinccoaManager` | class; JavaScript: `const { WinccoaManager } = require('winccoa-manager');` | everything | DOC `NodeJS/topics/nodejs_basic_configuration.html`, `NodeJS/topics/nodejs_javascript_module.html` | confirmed |
| `WinccoaConnectUpdateType` | enum `Normal = 0`, `Answer = 1` (initial update with current values), `Refresh = 2` (after REDU switch or DIST connection) | `Answer` and `Refresh` are sent as snapshots; `Refresh` also as a gap | API `enums/WinccoaConnectUpdateType.html` | confirmed |
| `WinccoaTimeFormat` | enum `Date = 0`, `Number = 1` (ms since epoch), `Temporal = 2` (`Temporal.Instant`, nanosecond precision) | `time_format` | API `enums/WinccoaTimeFormat.html` | confirmed |
| `WinccoaElementType` | enum; used members `Struct`, `Int`, `UInt`, `Long`, `ULong`, `Float`, `Bool`, `String`, `DynInt`, `DynUInt`, `DynLong`, `DynULong`, `DynFloat`, `DynBool`, `DynString` | namespace types; value types | API `enums/WinccoaElementType.html` | confirmed |
| `WinccoaDpTypeNode` | `new WinccoaDpTypeNode(name: string, type: WinccoaElementType, refName?: string, children?: WinccoaDpTypeNode[], newName?: string)` | namespace types | API `classes/WinccoaDpTypeNode.html` | confirmed |
| `WinccoaError` | class with `code`, `message`, `catalog`, `details` | error text in logs | API `classes/WinccoaError.html` | confirmed |

## Types and options

| Item | As documented | How the adapter relies on it | Source | Status |
|---|---|---|---|---|
| `WinccoaDpConnectCallback` | `(dpeNames: string[], values: unknown[], type: WinccoaConnectUpdateType, error?: WinccoaError) => void \| Promise<void>`; on error the other parameters are undefined | one callback per update; values in the order of `dpeNames` | API `types/WinccoaDpConnectCallback.html` | confirmed |
| `dpConnect` update content | "Each update will contain updates for all elements in dpeNames, not only the changed values" | value, source time and quality of one element arrive together | API `classes/WinccoaManager.html#dpconnect` | confirmed |
| `WinccoaDpQueryConnectCallback` | `(values: unknown[][], type, error?)`; row 0 is a header, each further row starts with the element name; an invalid query throws nothing and the first (only) callback carries the error | mode `query` | API `types/WinccoaDpQueryConnectCallback.html` | confirmed |
| `WinccoaOptions.longAsBigInt` | "long and unsigned long variables ... returned as BigInt"; default false | set to true: 64-bit integers stay exact | API `interfaces/WinccoaOptions.html`; DOC `NodeJS/topics/nodejs_valid_data_types.html` | confirmed |
| `WinccoaOptions.timeFormat` / `WinccoaTime` | `Date \| number \| Temporal.Instant`; default `Date` | `temporal` by default; if `setOptions` refuses it, `Date` (ms) and the status says so | API `interfaces/WinccoaOptions.html`, `types/WinccoaTime.html` | confirmed |
| `Temporal.Instant.epochNanoseconds` | BigInt nanoseconds since the epoch (TC39 Temporal, not a WinCC OA definition); the API loads `temporal-polyfill` itself | exact source time | API `enums/WinccoaTimeFormat.html` | confirmed that the format exists; the precision of WinCC OA's stored source times is **not** documented (question 3) |
| `WinccoaVersionDetails` | `{api: {version}, winccoa: {display, major, minor, numeric, numeric_full, patch, platform, revision, version}}` | status text | API `types/WinccoaVersionDetails.html` | confirmed |
| `registerExitCallback` behaviour | runs on `exit()` or a PMON exit signal, not on `process.exit()`; only the last registered callback runs | `stop()` | API `classes/WinccoaManager.html#registerexitcallback` | confirmed |
| Log mapping | `console.info/log` → `logInfo`, `console.warn` → `logWarning`, `console.error` → `logSevere` | the adapter calls the methods directly | API `classes/WinccoaManager.html#loginfo` | confirmed |
| Script and arguments | `[Node.js options] [manager options] <script> [script arguments]`; arguments read through `process.argv`; path relative to the project's script directory | `index.js --config <file>` | DOC `NodeJS/topics/nodejs_basic_configuration.html` | confirmed; the directory name differs between pages (see below) |
| Module format | the JavaScript template is CommonJS (`'use strict'`, `require`) | the adapter is CommonJS | DOC `NodeJS/topics/nodejs_javascript_module.html` | confirmed |
| Requirements | WinCC OA 3.20 or later; Node.js 22 LTS installed with WinCC OA; part of the Server Basic licence | CI uses Node 22 | DOC `NodeJS/topics/nodejs_requirements.html` | confirmed |

## Config attributes

Attribute names come from the WinCC OA config reference, DOC `Notes/dpconfig_original.html` (and
`Referenz_PARA/Referenz_PARA-19.html`); the address form `<dpe>:_original.._value` from the `dpAttributeType` example
in API `classes/WinccoaManager.html#dpattributetype`.

| Attribute | Meaning (config reference) | Adapter setting | Status |
|---|---|---|---|
| `_original.._value` | the original value | `subscription.value_attribute` | name confirmed; delivery through `dpConnect` with an attribute address **unconfirmed, verify in phase C** |
| `_original.._stime` | "The source time. The time that specifies when a value was changed the last time." | `subscription.stime_attribute` | name confirmed; appears in the documented one-shot `dpQuery` example (`SELECT '_original.._stime', '_original.._value' FROM 'ExampleDP_Arg*'`); delivery through `dpConnect` or `dpQueryConnectSingle` **unconfirmed, verify in phase C** (question 1) |
| `_original.._bad` | "virtual == _invalid \|\| _out_prange": the value is invalid or the value range was violated | `subscription.quality_attributes.bad` | name confirmed; delivery **unconfirmed, verify in phase C** (question 2) |
| `_original.._uncertain` | set for poll-on-use addresses that are not currently polled | `subscription.quality_attributes.uncertain` | name confirmed; delivery **unconfirmed, verify in phase C** (question 2) |

The verdict "good when `bad` and `uncertain` are false" (`subscription.good_when`) is the adapter's choice, not a
WinCC OA definition; the raw attributes are sent as well, so the companion keeps them for audit.

## Not confirmed, and how the adapter copes

1. **Attribute addresses in `dpConnect`.** The reference says "DPE name(s) to connect to". If the subscription with
   attributes is refused, the adapter falls back to the plain element: values without source time and quality,
   sent as `source_status: "unreadable"`, `quality_status: "unreadable"`, which become no context. The status
   datapoint says `degraded` and names the datapoint.
2. **The query form** `SELECT '<value>', '<stime>', '<quality>...' FROM '<one full element name>'`: the documented
   examples name datapoint patterns (`'ExampleDP_Arg*'`), not one element. Mode `query` is a setting so phase C can
   compare it with mode `connect`.
3. **Quality semantics** for the companion (`quality.good`), see above.
4. **Source time precision** stored by WinCC OA (question 3): the adapter sends what the API delivers, never more.
5. **Loss of the event-manager connection** (question 5) is not described for the JavaScript API. The adapter
   treats `Refresh` updates as possible loss for that datapoint (gap, then snapshot) and every start as a gap.
6. **Directory of scripts.** The basic-configuration page says the path is relative to the project's `nodejs`
   directory; the JavaScript-module page says `javascript/<modulename>`; the template's comment says
   `data/nodejs`. To be settled on an installation.
7. **`dpExists` semantics** ("at least one part ... can be resolved"): the bootstrap confirms an existing
   datapoint with `dpTypeName` and treats a mismatch as a conflict.
8. **Creating types from JavaScript at all** depends on the manager's user rights in the project; if
   `dpTypeCreate` is refused, the types can be created by hand in PARA (docs/winccoa.md, bootstrap). Unconfirmed.
9. **Missing evidence** (not found in the documentation, so not used): any event for "connection to EVENT lost",
   a documented reconnect of `dpConnect`, the installed examples of a 3.21 patch.
