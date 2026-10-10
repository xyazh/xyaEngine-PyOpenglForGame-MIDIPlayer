@echo off
REM ===========================================================================
REM  One-click packaging: any demo -> standalone (NOT onefile) Windows build.
REM
REM  Usage:   build.bat [demo.py]        default: Game.py
REM           build.bat Game4.py
REM  Output:  dist\<demo>.dist\<demo>.exe   (keep the WHOLE .dist folder)
REM
REM  Optional switches (environment variables; all default to OFF):
REM    XYA_SLIM=1      also drop cv2 (~95 MB); the player still runs, only
REM                    SETTINGS -> SAVE VIDEO reports "missing cv2". See the note
REM                    above the compile step.
REM    XYA_FULL=1      no size trimming at all = the previous command line.
REM    XYA_LTO=1       --lto=yes (a bit smaller exe, much slower link; rule 5).
REM    XYA_PACK_FULL=1 pack all of res\ (audio\ + movies\ included).
REM    XYA_PACK_COMPRESS=store|deflate|auto|max    (default: max = smallest pack)
REM    XYA_KEEP_PROFILER=1  ship line_profiler even for a demo that never calls it
REM    XYA_SMOKE=1     after the build, start the exe for 10 s and report whether
REM                    it stayed alive (a cheap "does it still import" check).
REM    XYA_REPORT=1    also write the Nuitka report XML into <stem>.build\
REM    XYA_DROP_UNUSED=1  also delete the two stdlib modules this app never
REM                    imports (unicodedata.pyd 1.07 MB, _queue.pyd 28 KB);
REM                    that costs the idna codec / queue - see the prune step.
REM    XYA_CLEAN=1     delete dist\<demo>.build (~300 MB of generated C/obj) once
REM                    the build succeeded; next build starts from scratch.
REM
REM  This file is plain ASCII on purpose: a .bat with UTF-8 Chinese comments is
REM  read as the OEM codepage (CP936 here) and the mojibake gets EXECUTED as
REM  commands. The Chinese write-up lives in docs instead (see docs folder).
REM
REM  ---------------------------------------------------------------------------
REM  Rules this script follows
REM  ---------------------------------------------------------------------------
REM  1) NO ABSOLUTE PATHS. Everything is relative to this file (%~dp0) or comes
REM     out of tools\ (those derive their own paths from __file__):
REM       - interpreter: the "py -3.10" launcher, else python on PATH (checked);
REM       - dependencies: the PROJECT'S OWN copies in pyglgame\libs (vendored by
REM         tools\vendor_libs.py, handed to Nuitka via PYTHONPATH=pyglgame\libs);
REM       - resources: packed from res\ by tools\pack_resources.py.
REM     So building on another machine needs only this repo + CPython 3.10 + Nuitka.
REM  2) --standalone, never --onefile: the engine resolves everything relative to
REM     the executable (pyglgame\tools\paths.py, openal\Device.py, PyOpenGL DLLS).
REM     --onefile unpacks to a temp dir and breaks all of that.
REM  3) Resources ship as a PACK (res.xypack) next to the exe, so the packaged
REM     build exercises the same code path as a shipped game (docs: pack doc).
REM     Default pack = res\ minus audio\**, movies\**, save\** (~12 MB) and the
REM     biggest entry (res\fonts\ygblack.ttf, 11.4 MB) goes in as LZMA:
REM     --compress max = "text + fonts LZMA, png/ogg/mp4 stored" and it turns
REM     res.xypack from 12.4 MB into 5.9 MB. Set XYA_PACK_FULL=1 to pack all of
REM     res\ and XYA_PACK_COMPRESS=auto|deflate|store to override the strategy
REM     (auto/deflate are the strategies the engine itself knows - max is applied
REM     by tools\pack_resources.py afterwards, see that file).
REM  4) --assume-yes-for-downloads: without it Nuitka asks an interactive question
REM     about downloading Dependency Walker and hangs forever when scripted.
REM  5) --lto=no: --lto=yes is what an older full build died on here
REM     ("Scons C backend compilation", see nuitka-crash-report.xml); it is
REM     available again as XYA_LTO=1 if you want to retry.
REM  6) Size first: three layers, each of them only removing what this project
REM     **provably never loads**, and each one already validated once:
REM       a) the resource pack (rule 3): 12.4 MB -> 5.9 MB;
REM       b) the Nuitka command line (see the trimming step): nofollow /
REM          noinclude-custom-mode for modules no demo imports, --noinclude-dlls
REM          for the DLL copies this platform never loads (nuitka
REM          --list-package-dlls=OpenGL reports all 12 freeglut/gle variants, the
REM          win32 platform module only ever uses vc14/64 of them);
REM       c) tools\prune_dist.py after the build: the same DLL rules applied to
REM          whatever Nuitka really produced (Tk family, 32-bit/vc9/vc10 copies,
REM          the Linux libopenal32.so), so a Nuitka upgrade that copies a whole
REM          package directory cannot silently add megabytes back.
REM     The one real giant left is cv2 (~95 MB: cv2.pyd 68 MB + its ffmpeg DLL
REM     27 MB) and stays opt-in off with XYA_SLIM=1 - the call site in
REM     WaterfallPage.VideoRecorder._onFrame already catches ImportError, so the
REM     player still runs and only SETTINGS -> SAVE VIDEO says "missing cv2".
REM ===========================================================================
setlocal enabledelayedexpansion
cd /d "%~dp0"

set "TARGET=%~1"
if not defined TARGET set "TARGET=Game.py"
for %%F in ("%TARGET%") do set "STEM=%%~nF"
set "PACK=dist\res.xypack"
set "DIST=dist\%STEM%.dist"
set "PY="

echo [build] target : %TARGET%
echo [build] output : dist\%STEM%.dist\%STEM%.exe

REM --- 1) interpreter: py -3.10, else python (must really be 3.10) -----------
py -3.10 -c "import sys" >nul 2>nul && set "PY=py -3.10"
if not defined PY (
  python -c "import sys; raise SystemExit(0 if sys.version_info[:2]==(3,10) else 1)" >nul 2>nul && set "PY=python"
)
if not defined PY (
  echo [build] FAILED: need the game interpreter - CPython 3.10.
  echo [build]   Install it with the "py" launcher, or put a 3.10 python on PATH.
  exit /b 1
)
echo [build] python : %PY%
%PY% -c "import sys; print('[build] version:', sys.version.split()[0])"

REM --- 1b) interpreter bitness (the DLL trimming below depends on it) --------
REM Nuitka copies every freeglut/gle variant found in OpenGL\DLLS and both
REM OpenAL DLLs; OpenGL/platform/win32.py and openal/Device.py pick ONE of them
REM from platform.architecture() at runtime, so only that one has to ship.
for /f "delims=" %%A in ('%PY% -c "import struct;print(struct.calcsize('P')*8)"') do set "ARCH=%%A"
if not defined ARCH set "ARCH=64"
echo [build] arch   : %ARCH%-bit

REM --- 2) build tools present? ----------------------------------------------
%PY% -m nuitka --version >nul 2>nul
if errorlevel 1 (
  echo [build] FAILED: Nuitka is not available for this interpreter.
  echo [build]   %PY% -m pip install nuitka
  exit /b 1
)

REM --- 3) vendored third-party dependencies (pyglgame\libs) -----------------
%PY% tools\vendor_libs.py --verify
if errorlevel 1 (
  echo [build] FAILED: pyglgame\libs is incomplete - see the message above.
  echo [build]   Refresh it with: %PY% tools\vendor_libs.py
  exit /b 1
)

REM --- 3b) third-party license bundle (LICENSES\ + THIRD_PARTY_NOTICES.md) ---
REM Both are generated by tools\collect_licenses.py and shipped next to the exe
REM (see the --include-data-files / --include-data-dir options below).
REM --verify also fails when a vendored package was upgraded and the bundle is
REM now stale, so the shipped notices always match what is really inside.
%PY% tools\collect_licenses.py --verify
if errorlevel 1 (
  echo [build] FAILED: third-party license bundle is missing or stale.
  echo [build]   Refresh it with: %PY% tools\collect_licenses.py
  exit /b 1
)

REM --- 4) pack resources (res\ -> dist\res.xypack) --------------------------
REM Kept as a plain command + variable (no if/else block, no caret continuation):
REM inside a parenthesised block cmd treats "^"+newline and unescaped "()" oddly,
REM which here ran the pack twice and swallowed the echo.
REM --compress max: the gap this closes is that "auto" only deflates
REM TEXT_SUFFIXES (.frag/.vert/.glsl/.json/...) and fonts are NOT in that list,
REM so the old default shipped the raw 11.4 MB ygblack.ttf inside the pack.
REM max = LZMA for text + fonts (5.35 MB for that font), store for png (nothing
REM to gain there): res.xypack 12.4 MB -> 5.9 MB. The repack pass lives in
REM tools\pack_resources.py (recompressPack) - the engine's own packDirectory
REM still decides entry names/order/manifest, so the package stays identical
REM except for the per-entry compression method ("max" is recorded in the
REM manifest). Override with XYA_PACK_COMPRESS=auto|deflate|store.
set "PACK_ARGS=--exclude audio/** --exclude movies/** --exclude save/**"
if defined XYA_PACK_FULL set "PACK_ARGS="
set "PACK_COMPRESS=%XYA_PACK_COMPRESS%"
if not defined PACK_COMPRESS set "PACK_COMPRESS=max"
echo [build] pack   : res\ -^> dist\res.xypack  [%PACK_ARGS%] compress=%PACK_COMPRESS%
%PY% tools\pack_resources.py --out "%PACK%" --compress %PACK_COMPRESS% %PACK_ARGS%
if errorlevel 1 (
  echo [build] FAILED: packing res\ failed.
  exit /b 1
)

REM --- 5) size trimming -------------------------------------------------------
REM Everything here only removes code/DLLs this project provably never loads.
REM How each claim was checked (so it can be re-checked after an upgrade):
REM   * modules:  py -X importtime -c "import MIDIPlayer.<every page>" and a grep
REM     of the whole repo - none of the modules below show up as an import;
REM   * DLLs:     nuitka --list-package-dlls=OpenGL / =pyglgame.openal show what
REM     Nuitka would copy, and OpenGL/platform/win32.py + openal/Device.py decide
REM     at runtime which one of them is ever loaded (vc14 + the right bitness).
REM
REM   --noinclude-*-mode=nofollow   pytest/unittest/setuptools/pydoc/IPython/
REM                                dask/numba support code that frozen stdlib
REM                                modules would otherwise carry along
REM   --nofollow-import-to=tkinter only OpenGL.Tk + PIL.ImageTk import it and
REM                                the engine imports neither: drops tcl86t.dll
REM                                (1.8 MB) + tk86t.dll (1.5 MB) + _tkinter.pyd.
REM                                noinclude-custom-mode is the stronger variant of
REM                                the same switch ("included as part of the
REM                                non-excluded parts of standard library")
REM   --nofollow-import-to=*.tests the vendored packages' test suites
REM   --noinclude-custom-mode=<mod>:nofollow  stdlib extras that no import in
REM                                this repo reaches but Nuitka still ships:
REM                                  asyncio      (_asyncio + _overlapped, 0.11 MB;
REM                                                only numpy's tests and one
REM                                                line_profiler docstring name it)
REM                                  decimal      (_decimal, 0.24 MB; only tests)
REM                                  sqlite3      (nothing - belt and braces)
REM                                Measured effect in the 2026-10 MIDIPlayer build:
REM                                _decimal.pyd, _asyncio.pyd, _overlapped.pyd and
REM                                _multiprocessing.pyd all disappeared; pyexpat.pyd
REM                                went with them. unicodedata.pyd (1.07 MB) did
REM                                NOT - Nuitka ships it "as part of the non-excluded
REM                                parts of standard library" (checked with a
REM                                hello-world probe too, so it is not this app's
REM                                graph) and no follow/custom-mode flag stops it;
REM                                the only user is the idna codec (i.e. the bundled
REM                                HTTP client encoding a hostname), so it is left
REM                                alone by default and tools\prune_dist.py can drop
REM                                it with --drop-unused (XYA_DROP_UNUSED=1).
REM   --noinclude-dlls=...         dead DLL copies that Nuitka's package scan copies
REM                                wholesale (see the --list-package-dlls commands
REM                                above; tools\prune_dist.py has the long version):
REM                                  freeglut32.* + gle32.*    32-bit builds
REM                                  *.vc9.dll + *.vc10.dll    pre-3.5 VC runtimes
REM                                  libopenal32.so            Linux
REM                                  OpenAL32.dll              32-bit builds
REM                                (64-bit vc14 freeglut/gle + OpenAL64.dll stay -
REM                                those are what win32.py / Device.py load)
REM   --python-flag=no_docstrings  docstrings would otherwise be baked into the
REM                                constants blob (the engine reads *source* for
REM                                @glCompute, and compilation removes that
REM                                anyway - unrelated to this switch)
REM
REM XYA_FULL=1 restores the previous, untrimmed command line.
set "TRIM="
if not defined XYA_FULL (
  set "TRIM=--noinclude-pytest-mode=nofollow --noinclude-unittest-mode=nofollow --noinclude-setuptools-mode=nofollow --noinclude-pydoc-mode=nofollow --noinclude-IPython-mode=nofollow --noinclude-dask-mode=nofollow --noinclude-numba-mode=nofollow --nofollow-import-to=tkinter --noinclude-custom-mode=tkinter:nofollow --nofollow-import-to=*.tests --nofollow-import-to=asyncio --noinclude-custom-mode=asyncio:nofollow --nofollow-import-to=decimal --noinclude-custom-mode=decimal:nofollow --nofollow-import-to=sqlite3 --python-flag=no_docstrings"
)
if not defined XYA_FULL if "%ARCH%"=="64" (
  set "TRIM=!TRIM! --noinclude-dlls=*/freeglut32.* --noinclude-dlls=*/gle32.* --noinclude-dlls=*.vc9.dll --noinclude-dlls=*.vc10.dll --noinclude-dlls=*/libopenal32.so --noinclude-dlls=*/OpenAL32.dll"
)
if not defined XYA_FULL if "%ARCH%"=="32" (
  set "TRIM=!TRIM! --noinclude-dlls=*/freeglut64.* --noinclude-dlls=*/gle64.* --noinclude-dlls=*.vc9.dll --noinclude-dlls=*.vc10.dll --noinclude-dlls=*/libopenal32.so --noinclude-dlls=*/OpenAL64.dll"
)

REM line_profiler (its .pyd + tomli/typing_extensions + the rc\ data, ~2.4 MB) is
REM Game4's per-line profiler. Nothing in MIDIPlayer/MIDIPlayer.py imports it, so
REM only Game4.py gets it; XYA_KEEP_PROFILER=1 forces it back on for a new demo.
set "PROF="
set "NEED_PROFILER="
if /i "%STEM%"=="Game4" set "NEED_PROFILER=1"
if defined XYA_KEEP_PROFILER set "NEED_PROFILER=1"
if defined NEED_PROFILER set "PROF=--include-module=line_profiler.rc --include-data-dir=./pyglgame/libs/line_profiler/rc=line_profiler/rc"

REM XYA_SLIM=1 trades the optional video recording for ~95 MB: cv2 comes from
REM opencv-python, cv2.pyd (68 MB) + opencv's own ffmpeg dll (27 MB). It is
REM imported deep inside WaterfallPage.VideoRecorder._onFrame and that call site
REM already catches ImportError, so the player still runs and only
REM SETTINGS -> SAVE VIDEO reports "missing cv2/numpy".
set "NOFOLLOW="
if not defined NEED_PROFILER set "NOFOLLOW=--nofollow-import-to=line_profiler"
if defined XYA_SLIM (
  set "NOFOLLOW=!NOFOLLOW! --nofollow-import-to=cv2"
  echo [build] slim   : cv2 excluded ^(no video recording^)
)

REM XYA_LTO=1 turns on link time optimization (smaller exe, slower link).
set "LTO=--lto=no"
if defined XYA_LTO set "LTO=--lto=yes"

REM XYA_REPORT=1 writes Nuitka's module report into the build dir (analysis only,
REM it never ships): it answers "why is this module/DLL in here" after an upgrade.
set "REPORT="
if defined XYA_REPORT set "REPORT=--report=dist/%STEM%.build/report.xml --report-diffable"

REM --- 6) compile -----------------------------------------------------------
REM PYTHONPATH points Nuitka at the project's own dependency copies, so the build
REM uses exactly the same numpy/PIL/OpenGL that the game will use at runtime.
REM The include-data-files lines for the two vc14/64-bit GLUT/GLE DLLs and for
REM OpenAL64.dll are deliberate: Nuitka would copy the whole DLLS lib/ directory
REM on its own (--list-package-dlls above), so naming the one file this platform
REM loads documents the dependency AND keeps the "if this is missing the build is
REM broken" check below meaningful (tools\prune_dist.py --check repeats it).
REM The 32-bit / vc9 / vc10 / Linux copies are excluded in %TRIM%; the old
REM --include-raw-dir calls that used to ship them are gone.
REM Not shipped (on purpose): midi\ and background\. MidiListPage.midiDirectory
REM resolves "midi" next to the working directory at runtime (os.getcwd()) and
REM creates it when missing, so songs live outside the exe by design; the same
REM goes for the background\*.png of config.json. If a release should carry the
REM repo's samples anyway, append:
REM   --include-data-files="./midi/*.mid=midi/"
REM   --include-data-files="./background/*.png=background/"
set "PYTHONPATH=pyglgame\libs"

%PY% -m nuitka ^
  --standalone ^
  --output-dir=./dist ^
  --output-filename="%STEM%.exe" ^
  --include-data-files="%PACK%"=res.xypack ^
  --include-data-files="./THIRD_PARTY_NOTICES.md"=THIRD_PARTY_NOTICES.md ^
  --include-data-dir=./LICENSES=LICENSES ^
  --include-data-files="./pyglgame/openal/lib/OpenAL64.dll=pyglgame/openal/lib/OpenAL64.dll" ^
  --include-data-files="./pyglgame/libs/OpenGL/DLLS/freeglut64.vc14.dll=OpenGL/DLLS/freeglut64.vc14.dll" ^
  --include-data-files="./pyglgame/libs/OpenGL/DLLS/gle64.vc14.dll=OpenGL/DLLS/gle64.vc14.dll" ^
  --include-module=OpenGL_accelerate.formathandler ^
  %PROF% ^
  %TRIM% ^
  %NOFOLLOW% ^
  %REPORT% ^
  --assume-yes-for-downloads ^
  --windows-console-mode=force ^
  %LTO% ^
  "./%TARGET%"
if errorlevel 1 (
  echo [build] FAILED: see Nuitka output above / nuitka-crash-report.xml
  exit /b 1
)

REM --- 6b) prune whatever Nuitka copied that this platform never loads --------
REM Belt and braces after %TRIM%: the same rules applied to what really landed in
REM the dist (Tk family, 32/64-bit mismatch, vc9/vc10, Linux .so, pdb, pycache,
REM numpy's test-only extension, PIL's Tk plugin). If a Nuitka upgrade starts
REM copying a package directory wholesale again, this keeps the shipped folder
REM small instead of silently growing it. XYA_DROP_UNUSED=1 adds the two stdlib
REM modules no code path in this repo imports (see the header switch list).
set "PRUNE_ARGS=--arch %ARCH% --check"
if defined XYA_DROP_UNUSED set "PRUNE_ARGS=%PRUNE_ARGS% --drop-unused"
%PY% tools\prune_dist.py "%DIST%" %PRUNE_ARGS%
if errorlevel 1 (
  echo [build] FAILED: dist is missing a platform-required file ^(see above^).
  exit /b 1
)

REM --- 7) sanity check the dist --------------------------------------------
set "EXPECT=%STEM%.exe res.xypack THIRD_PARTY_NOTICES.md LICENSES\INDEX.md pyglgame\openal\lib\OpenAL64.dll OpenGL\DLLS\freeglut64.vc14.dll OpenGL\DLLS\gle64.vc14.dll OpenGL_accelerate\formathandler.pyd"
if defined NEED_PROFILER set "EXPECT=!EXPECT! line_profiler\rc\line_profiler.toml"
set "UNEXPECTED="
if not defined XYA_FULL if "%ARCH%"=="64" set "UNEXPECTED=pyglgame\openal\lib\libopenal32.so pyglgame\openal\lib\OpenAL32.dll OpenGL\DLLS\freeglut32.vc9.dll OpenGL\DLLS\gle64.vc10.dll tcl86t.dll tk86t.dll _tkinter.pyd"
set "MISSING="
for %%F in (%EXPECT%) do (
  if not exist "%DIST%\%%~F" set "MISSING=!MISSING! %%~F"
)
set "EXTRA="
for %%F in (%UNEXPECTED%) do (
  if exist "%DIST%\%%~F" set "EXTRA=!EXTRA! %%~F"
)
if defined MISSING (
  echo [build] WARNING: missing in dist:!MISSING!
) else (
  echo [build] dist   : all expected files present
)
if defined EXTRA echo [build] WARNING: should have been trimmed:!EXTRA!

REM --- 8) size report (the number the switches above exist for) --------------
REM No "%" formatting inside the -c string on purpose: cmd would eat it.
%PY% -c "import os,sys;d=sys.argv[1];f=[(os.path.getsize(os.path.join(r,n)),os.path.relpath(os.path.join(r,n),d)) for r,_,ns in os.walk(d) for n in ns];f.sort(reverse=True);print('[build] size   :',len(f),'files,',round(sum(s for s,_ in f)/1048576.0,1),'MB');[print('[build]   {0:8.1f} MB  {1}'.format(s/1048576.0,p)) for s,p in f[:8]]" "%DIST%"

REM --- 9) optional smoke test (XYA_SMOKE=1) ---------------------------------
REM Start the exe, wait 10 s, then check whether it is still alive. This catches
REM "the build got smaller because a module/DLL it needs is gone" without having
REM to click through the player; the player opens a window, so it is opt-in.
if defined XYA_SMOKE (
  echo [build] smoke  : starting %DIST%\%STEM%.exe for 10 s ...
  start "" "%DIST%\%STEM%.exe"
  ping -n 11 127.0.0.1 >nul
  tasklist /fi "imagename eq %STEM%.exe" | find /i "%STEM%.exe" >nul
  if !errorlevel! neq 0 (
    echo [build] smoke  : FAILED - it exited within 10 s ^(see the window output^)
  ) else (
    echo [build] smoke  : OK - still running after 10 s
  )
  taskkill /im "%STEM%.exe" /f >nul 2>nul
)

REM --- 10) optional: drop the (re)build directory (XYA_CLEAN=1) --------------
REM dist\<stem>.build holds ~300 MB of generated C/objects for incremental
REM rebuilds. It is only removed here - after everything above worked - so a
REM failed build keeps its intermediate files for debugging.
if defined XYA_CLEAN (
  echo [build] clean  : removing dist\%STEM%.build
  rmdir /s /q "dist\%STEM%.build"
)

echo.
echo [build] OK: %DIST%\%STEM%.exe
echo [build] Run it from that folder; keep the whole folder together.
echo [build] Smaller? rebuild with: set XYA_SLIM=1 %~nx0 %TARGET%  ^(drops cv2, ~95 MB^)
endlocal
