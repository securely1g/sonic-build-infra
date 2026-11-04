"""Real relocated Tcl/Expect and Valgrind memory-error execution contracts."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

source = Path(sys.argv[1]).absolute(); binary = Path(sys.argv[2]).absolute()
with tempfile.TemporaryDirectory(prefix='declared-test-tools-') as directory:
    work = Path(directory); root = work / 'relocated-runtime'
    shutil.copytree(source, root, symlinks=True)
    manifest = json.loads((root/'toolchain.json').read_text())
    env = {'PATH':'', 'HOME':str(work), 'LANG':'C', 'LC_ALL':'C', 'DEBUGINFOD_URLS':''}
    def run(tool, args, data=None):
        if tool == 'tclsh' and data is not None:
            script_file = work/'driver.tcl'; script_file.write_text(data)
            args = [str(script_file), *args]; data = None
        return subprocess.run([str(root/'bin'/tool),*args], input=data, env=env, cwd=work, capture_output=True, text=True)
    r = run('valgrind',['--version']); assert r.returncode == 0 and r.stdout.startswith('valgrind-'), r
    child = work/'interactive.tcl'
    child.write_text('gets stdin line\nputs [expr {$line * 2}]\n')
    script = r'''package require tcltest
package require Expect
set timeout 10
spawn {TCLSH} {CHILD}
send "21\r"
expect {
    -re {42[\r\n]+} {}
    timeout {error "declared child produced no result"}
    eof {error "declared child exited without result"}
}
expect eof
set result [wait]
if {[lindex $result 3] != 0} {exit 1}
puts TCL_EXPECT_PASS
'''.replace('TCLSH', str(root/'bin/tclsh')).replace('CHILD', str(child))
    r = run('tclsh',[],script); assert r.returncode == 0 and 'TCL_EXPECT_PASS' in r.stdout, (r.stdout,r.stderr)
    suite = work/'suite'; suite.mkdir()
    (suite/'child.test').write_text('package require tcltest\npackage require Expect\n::tcltest::test child-1 {separate interpreter executes declared Tcl} -body {expr {6*7}} -result 42\n::tcltest::cleanupTests\n')
    script = 'package require tcltest\n::tcltest::configure -testdir {'+str(suite)+'}\n::tcltest::runAllTests\n'
    r = run('tclsh', [], script)
    assert r.returncode == 0 and 'Total\t1' in r.stdout and 'Passed\t1' in r.stdout and 'exiting with errors' not in r.stdout+r.stderr, (r.stdout, r.stderr)
    loader = root/manifest['loader']; libraries = ':'.join(str(root/path) for path in manifest['library_directories'])
    common = ['--tool=memcheck','--error-exitcode=97','--leak-check=full','--errors-for-leak-kinds=definite','--extra-debuginfo-path='+str(root/'usr/lib/debug'),str(loader),'--library-path',libraries,str(binary)]
    for arguments, expected in [([],0),(['leak'],97),(['read'],97)]:
        r = run('valgrind',common+arguments)
        assert r.returncode == expected, (arguments,r.returncode,r.stdout,r.stderr)
        if arguments == ['read']: assert 'Invalid read' in r.stderr, r.stderr
        if arguments == ['leak']: assert 'definitely lost' in r.stderr, r.stderr
