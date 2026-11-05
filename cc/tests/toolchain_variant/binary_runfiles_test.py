"""Run the transitioned ELF through its declared loader and runfiles."""
import subprocess
import sys
subprocess.run([sys.argv[1]], check=True)
