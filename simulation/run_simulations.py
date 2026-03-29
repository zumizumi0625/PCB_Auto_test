#!/usr/bin/env python3
"""
SPICE Simulation Test Runner for CI

Runs all .spice files in the simulation/ directory using ngspice
and parses results to determine pass/fail.
On failure, generates waveform plots as PNG images.

Usage:
    python3 simulation/run_simulations.py

Exit codes:
    0 - All simulations passed
    1 - One or more simulations failed
"""

import subprocess
import sys
import os
from pathlib import Path


def generate_waveform_plot(spice_file: Path, output_dir: Path):
    """Generate waveform PNG for a failed simulation using gnuplot via ngspice."""
    plot_script = spice_file.parent / "_plot_temp.spice"
    png_path = output_dir / f"{spice_file.stem}_waveform.png"

    # Read original spice file and inject plot commands
    content = spice_file.read_text()

    # Find the .control block and inject plot-to-file before quit
    if ".control" in content and "quit" in content:
        plot_commands = f"""
  * === Auto-generated waveform plot ===
  set hcopydevtype = png
  set hcopywidth = 1200
  set hcopyheight = 600
  hardcopy {png_path} allv
"""
        content = content.replace("  quit", plot_commands + "  quit")
        plot_script.write_text(content)

        try:
            subprocess.run(
                ["ngspice", "-b", str(plot_script)],
                capture_output=True,
                timeout=60,
                cwd=spice_file.parent,
            )
        except Exception:
            pass
        finally:
            plot_script.unlink(missing_ok=True)

    if png_path.exists():
        print(f"  Waveform saved: {png_path}")
        return str(png_path)
    return None


def run_ngspice(spice_file: Path, output_dir: Path) -> dict:
    """Run a single ngspice simulation and parse results."""
    results_file = spice_file.parent / "simulation_results.txt"

    # Clean up previous results
    if results_file.exists():
        results_file.unlink()

    print(f"\n{'='*60}")
    print(f"Running: {spice_file.name}")
    print(f"{'='*60}")

    try:
        proc = subprocess.run(
            ["ngspice", "-b", str(spice_file)],
            capture_output=True,
            text=True,
            timeout=120,
            cwd=spice_file.parent,
        )

        print(proc.stdout[-500:] if len(proc.stdout) > 500 else proc.stdout)
        if proc.stderr:
            errors = [
                line for line in proc.stderr.splitlines()
                if not line.startswith("Note:") and "Reducing" not in line
            ]
            if errors:
                print("STDERR:", "\n".join(errors[-10:]))

    except FileNotFoundError:
        print("ERROR: ngspice not found. Install with: apt install ngspice")
        return {"status": "ERROR", "message": "ngspice not installed"}
    except subprocess.TimeoutExpired:
        print("ERROR: Simulation timed out (120s)")
        return {"status": "FAIL", "message": "timeout"}

    # Parse results file
    result = {"status": "UNKNOWN", "values": {}}

    if results_file.exists():
        for line in results_file.read_text().splitlines():
            if line.startswith("RESULT:"):
                key, val = line[7:].split("=", 1)
                result["values"][key] = val
            elif line.startswith("STATUS:"):
                result["status"] = line[7:].strip()
    else:
        if proc.returncode == 0:
            result["status"] = "PASS"
        else:
            result["status"] = "FAIL"
            result["message"] = f"ngspice exited with code {proc.returncode}"

    # Generate waveform plot on failure
    if result["status"] == "FAIL":
        print("  Generating waveform plot for failed test...")
        waveform = generate_waveform_plot(spice_file, output_dir)
        if waveform:
            result["waveform"] = waveform

    return result


def main():
    sim_dir = Path(__file__).parent
    output_dir = sim_dir.parent / "output" / "waveforms"
    output_dir.mkdir(parents=True, exist_ok=True)

    spice_files = sorted(sim_dir.glob("*.spice"))

    if not spice_files:
        print("No .spice files found in simulation/")
        return 0

    print(f"Found {len(spice_files)} simulation(s) to run")
    print(f"Waveform output: {output_dir}")

    results = {}
    for spice_file in spice_files:
        results[spice_file.name] = run_ngspice(spice_file, output_dir)

    # Summary
    print(f"\n{'='*60}")
    print("SIMULATION RESULTS SUMMARY")
    print(f"{'='*60}")

    passed = 0
    failed = 0
    errors = 0

    for name, result in results.items():
        status = result["status"]
        icon = "✅" if status == "PASS" else "❌" if status == "FAIL" else "⚠️"
        print(f"  {icon} {name}: {status}")
        if result.get("values"):
            for k, v in result["values"].items():
                print(f"      {k} = {v}")
        if result.get("waveform"):
            print(f"      waveform: {result['waveform']}")

        if status == "PASS":
            passed += 1
        elif status == "FAIL":
            failed += 1
        else:
            errors += 1

    print(f"\nTotal: {passed} passed, {failed} failed, {errors} errors")

    return 1 if (failed > 0 or errors > 0) else 0


if __name__ == "__main__":
    sys.exit(main())
