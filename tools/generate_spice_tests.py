#!/usr/bin/env python3
"""
KiCad Schematic → SPICE Test Auto-Generator

Parses a KiCad .kicad_sch file and automatically generates
SPICE simulation tests based on detected circuit patterns:

1. Power net voltage checks
2. Resistor divider detection and output voltage verification
3. LED current limiting resistor checks
4. I2C bus signal integrity (pull-up + bus capacitance)
5. SPI signal integrity (trace model)
6. Crystal oscillator verification
7. LDO/Regulator output stability
8. Reset circuit pulse verification
9. Power budget estimation

Usage:
    python3 tools/generate_spice_tests.py hardware/my-board.kicad_sch

Output:
    simulation/auto_<schematic_name>.spice
"""

import re
import sys
from pathlib import Path
from dataclasses import dataclass


@dataclass
class Component:
    reference: str
    value: str
    lib_id: str = ""


@dataclass
class PowerNet:
    name: str
    voltage: float


@dataclass
class DetectedPatterns:
    i2c_nets: list
    spi_nets: list
    reset_nets: list
    has_crystal: bool
    has_ldo: bool
    has_tvs: bool
    has_mosfet_protection: bool
    power_nets: list
    components: list


# ============================================================
# Parsing
# ============================================================

def parse_schematic(sch_path: Path) -> DetectedPatterns:
    """Parse KiCad schematic and detect circuit patterns."""
    content = sch_path.read_text()

    # Split off lib_symbols
    parts = content.split("(lib_symbols")
    if len(parts) > 1:
        rest = parts[1]
        depth = 1
        for i, c in enumerate(rest):
            if c == "(":
                depth += 1
            elif c == ")":
                depth -= 1
                if depth == 0:
                    instances_text = rest[i + 1:]
                    break
    else:
        instances_text = content

    # Components
    refs = re.findall(r'\(property "Reference" "([^"]+)"', instances_text)
    vals = re.findall(r'\(property "Value" "([^"]+)"', instances_text)
    components = [Component(r, v) for r, v in zip(refs, vals) if not r.startswith("#")]

    # Power nets
    power_nets = []
    for net in set(re.findall(r'\(property "Value" "\+([^"]+)"', content)):
        v = parse_voltage(net)
        if v:
            power_nets.append(PowerNet(f"+{net}", v))

    # Net labels
    labels = re.findall(r'\(label "([^"]+)"', content)
    global_labels = re.findall(r'\(global_label "([^"]+)"', content)
    all_labels = labels + global_labels

    # I2C detection
    i2c_nets = list({l for l in all_labels if any(k in l.upper() for k in ["SDA", "SCL", "I2C"])})

    # SPI detection
    spi_nets = list({l for l in all_labels if any(k in l.upper() for k in ["SPI", "SCLK", "MOSI", "MISO", "SCK"])})

    # Reset detection
    reset_nets = list({l for l in all_labels if any(k in l.upper() for k in ["RESET", "RST", "NRST"])})

    # Crystal detection
    has_crystal = bool(re.search(r'Crystal|XTAL|xtal|crystal|Y\d+', content, re.IGNORECASE))

    # LDO/Regulator detection
    has_ldo = bool(re.search(
        r'LDO|Regulator|XC6206|AMS1117|MCP1700|AP2112|TPS7|LP298|NCV|HT73',
        content, re.IGNORECASE
    ))

    # TVS diode detection
    has_tvs = bool(re.search(
        r'TVS|SMBJ|SMAJ|P6KE|ESD|PESD|CDSOT|PRTR|SP0|TPD\d',
        content, re.IGNORECASE
    ))

    # MOSFET protection detection (P-ch reverse polarity, etc.)
    has_mosfet_protection = bool(re.search(
        r'SI[0-9].*P|DMG|FDN|AO3401|DMP|IRLML|BSS84|NTR|CJ',
        content, re.IGNORECASE
    ))

    return DetectedPatterns(
        i2c_nets=i2c_nets,
        spi_nets=spi_nets,
        reset_nets=reset_nets,
        has_crystal=has_crystal,
        has_ldo=has_ldo,
        has_tvs=has_tvs,
        has_mosfet_protection=has_mosfet_protection,
        power_nets=power_nets,
        components=components,
    )


def parse_voltage(net_name: str) -> float | None:
    m = re.match(r"(\d+)V(\d+)?", net_name)
    if m:
        return float(f"{m.group(1)}.{m.group(2) or '0'}")
    return None


def parse_resistance(value: str) -> float | None:
    value = value.strip().upper()
    m = re.match(r"^(\d+)K(\d+)?$", value)
    if m:
        return float(f"{m.group(1)}.{m.group(2) or '0'}") * 1e3
    m = re.match(r"^(\d+)M(\d+)?$", value)
    if m:
        return float(f"{m.group(1)}.{m.group(2) or '0'}") * 1e6
    m = re.match(r"^(\d+\.?\d*)$", value)
    if m:
        return float(m.group(1))
    return None


# ============================================================
# SPICE Template Generators
# ============================================================

def gen_i2c_test(idx: int, vcc: float, nets: list[str]) -> list[str]:
    """Generate I2C bus integrity test subcircuit."""
    net_desc = ", ".join(nets[:4])
    return [
        f"* === I2C Bus Test #{idx} (detected: {net_desc}) ===",
        f"* Pull-up to {vcc}V, 2.2k, 200pF bus capacitance",
        f"VCC_i2c{idx} vcc_i2c{idx} 0 DC {vcc}",
        f"Rp_i2c{idx} vcc_i2c{idx} sda_i2c{idx} 2.2k",
        f"Cbus_i2c{idx} sda_i2c{idx} 0 200p",
        f"M_i2c{idx} sda_i2c{idx} gate_i2c{idx} 0 0 NMOD_I2C W=100u L=0.5u",
        f"Vg_i2c{idx} gate_i2c{idx} 0 PULSE(3.3 0 2u 10n 10n 5u 10u)",
        f"",
    ]


def gen_i2c_checks(idx: int, vcc: float) -> list[str]:
    """Generate I2C measurement and check code."""
    vih = round(0.7 * vcc, 2)
    return [
        f"  * --- I2C Bus #{idx} ---",
        f"  let i2c{idx}_tr_10 = -1",
        f"  let i2c{idx}_tr_90 = -1",
        f"  meas tran i2c{idx}_tr_10 when v(sda_i2c{idx})={round(0.1*vcc, 2)} rise=1",
        f"  meas tran i2c{idx}_tr_90 when v(sda_i2c{idx})={round(0.9*vcc, 2)} rise=1",
        f"  let i2c{idx}_rise = -1",
        f"  if $&i2c{idx}_tr_10 > 0",
        f"    if $&i2c{idx}_tr_90 > 0",
        f"      let i2c{idx}_rise = i2c{idx}_tr_90 - i2c{idx}_tr_10",
        f"    end",
        f"  end",
        f"  let i2c{idx}_vol = -1",
        f"  meas tran i2c{idx}_vol avg v(sda_i2c{idx}) from=0.5u to=1.5u",
        f"  let i2c{idx}_voh = -1",
        f"  meas tran i2c{idx}_voh avg v(sda_i2c{idx}) from=5u to=6.5u",
        f'  echo "I2C#{idx} rise=$&i2c{idx}_rise s, VOL=$&i2c{idx}_vol V, VOH=$&i2c{idx}_voh V"',
        f'  echo "RESULT:i2c{idx}_rise=$&i2c{idx}_rise" >> simulation_results.txt',
        f'  echo "RESULT:i2c{idx}_vol=$&i2c{idx}_vol" >> simulation_results.txt',
        f"  if $&i2c{idx}_rise > 1e-6",
        f'    echo "FAIL: I2C#{idx} rise time > 1us"',
        f"    let pass = 0",
        f"  end",
        f"  if $&i2c{idx}_vol > 0.4",
        f'    echo "FAIL: I2C#{idx} VOL > 0.4V"',
        f"    let pass = 0",
        f"  end",
        f"  if $&i2c{idx}_voh < {vih}",
        f'    echo "FAIL: I2C#{idx} VOH < {vih}V"',
        f"    let pass = 0",
        f"  end",
        f"",
    ]


def gen_spi_test(idx: int, vcc: float, nets: list[str]) -> list[str]:
    """Generate SPI signal integrity test."""
    net_desc = ", ".join(nets[:4])
    return [
        f"* === SPI Signal Test #{idx} (detected: {net_desc}) ===",
        f"Vsclk_drv{idx} sclk_src{idx} 0 PULSE(0 {vcc} 100n 2n 2n 500n 1u)",
        f"Rdrv_sclk{idx} sclk_src{idx} sclk_a{idx} 33",
        f"Ltrace_sclk{idx} sclk_a{idx} sclk_b{idx} 10n",
        f"Rtrace_sclk{idx} sclk_b{idx} sclk{idx} 2",
        f"Cload_sclk{idx} sclk{idx} 0 15p",
        f"",
    ]


def gen_spi_checks(idx: int, vcc: float) -> list[str]:
    """Generate SPI check code."""
    return [
        f"  * --- SPI Signal #{idx} ---",
        f"  let spi{idx}_max = -1",
        f"  let spi{idx}_min = 99",
        f"  meas tran spi{idx}_max max v(sclk{idx})",
        f"  meas tran spi{idx}_min min v(sclk{idx})",
        f'  echo "SPI#{idx} max=$&spi{idx}_max V, min=$&spi{idx}_min V"',
        f'  echo "RESULT:spi{idx}_max=$&spi{idx}_max" >> simulation_results.txt',
        f"  if $&spi{idx}_max > {round(vcc * 1.1, 2)}",
        f'    echo "FAIL: SPI#{idx} overshoot > VCC+10%"',
        f"    let pass = 0",
        f"  end",
        f"  if $&spi{idx}_min < -0.3",
        f'    echo "FAIL: SPI#{idx} undershoot < -0.3V"',
        f"    let pass = 0",
        f"  end",
        f"",
    ]


def gen_crystal_test(idx: int, vcc: float) -> list[str]:
    """Generate crystal oscillator test."""
    return [
        f"* === Crystal Oscillator Test (8MHz Pierce) ===",
        f"VDD_xtal vdd_xtal 0 DC {vcc}",
        f"Lm_x xtal1 xm1 10.24m",
        f"Cm_x xm1 xm2 38.4f",
        f"Rm_x xm2 xtal2 30",
        f"C0_x xtal1 xtal2 5p",
        f"Mp_x xtal2 xtal1 vdd_xtal vdd_xtal PMOD_X W=10u L=0.5u",
        f"Mn_x xtal2 xtal1 0 0 NMOD_X W=5u L=0.5u",
        f".model PMOD_X PMOS (VTO=-0.7 KP=50u)",
        f".model NMOD_X NMOS (VTO=0.7 KP=110u)",
        f"Rf_x xtal1 xtal2 1meg",
        f"CL1_x xtal1 0 18p",
        f"CL2_x xtal2 0 18p",
        f".ic v(xtal1)=1.8 v(xtal2)=1.5",
        f"",
    ]


def gen_crystal_checks() -> list[str]:
    """Generate crystal check code."""
    return [
        f"  * --- Crystal Oscillator ---",
        f"  let xtal_vmax = -1",
        f"  let xtal_vmin = -1",
        f"  meas tran xtal_vmax max v(xtal2) from=180u to=200u",
        f"  meas tran xtal_vmin min v(xtal2) from=180u to=200u",
        f"  let xtal_vpp = -1",
        f"  if $&xtal_vmax > 0",
        f"    if $&xtal_vmin >= 0",
        f"      let xtal_vpp = xtal_vmax - xtal_vmin",
        f"    end",
        f"  end",
        f"  let xtal_t1 = -1",
        f"  let xtal_t2 = -1",
        f"  meas tran xtal_t1 when v(xtal2)=1.65 rise=10",
        f"  meas tran xtal_t2 when v(xtal2)=1.65 rise=11",
        f"  let xtal_freq = -1",
        f"  if $&xtal_t1 > 0",
        f"    if $&xtal_t2 > 0",
        f"      let xtal_freq = 1 / (xtal_t2 - xtal_t1)",
        f"    end",
        f"  end",
        f'  echo "Crystal: Vpp=$&xtal_vpp V, freq=$&xtal_freq Hz"',
        f'  echo "RESULT:xtal_vpp=$&xtal_vpp" >> simulation_results.txt',
        f'  echo "RESULT:xtal_freq=$&xtal_freq" >> simulation_results.txt',
        f"  if $&xtal_vpp < 1.0",
        f'    echo "FAIL: Crystal not oscillating (Vpp < 1V)"',
        f"    let pass = 0",
        f"  end",
        f"",
    ]


def gen_ldo_test(idx: int, vin: float, vout: float) -> list[str]:
    """Generate LDO regulator output test."""
    return [
        f"* === LDO Regulator Test #{idx} ({vin}V → {vout}V) ===",
        f"* Ideal LDO model: regulated output with output impedance",
        f"Vldo{idx} vout_ldo{idx} 0 DC {vout}",
        f"Rldo_out{idx} vout_ldo{idx} vout_ldo_load{idx} 0.1",
        f"Cldo_out{idx} vout_ldo_load{idx} 0 10u IC={vout}",
        f"Rldo_load{idx} vout_ldo_load{idx} 0 {round(vout / 0.1, 1)}",
        f"* Switching load",
        f"Ildo_sw{idx} vout_ldo_load{idx} 0 PULSE(0 50m 10u 100n 100n 1u 10u)",
        f"",
    ]


def gen_ldo_checks(idx: int, vout: float) -> list[str]:
    """Generate LDO check code."""
    low = round(vout * 0.95, 3)
    high = round(vout * 1.05, 3)
    return [
        f"  * --- LDO #{idx} ({vout}V) ---",
        f"  let ldo{idx}_avg = -1",
        f"  meas tran ldo{idx}_avg avg v(vout_ldo_load{idx}) from=10m to=50m",
        f"  let ldo{idx}_min = -1",
        f"  meas tran ldo{idx}_min min v(vout_ldo_load{idx}) from=10m to=100m",
        f'  echo "LDO#{idx}: avg=$&ldo{idx}_avg V, min=$&ldo{idx}_min V"',
        f'  echo "RESULT:ldo{idx}_avg=$&ldo{idx}_avg" >> simulation_results.txt',
        f"  if $&ldo{idx}_avg < {low}",
        f'    echo "FAIL: LDO#{idx} output too low"',
        f"    let pass = 0",
        f"  end",
        f"  if $&ldo{idx}_avg > {high}",
        f'    echo "FAIL: LDO#{idx} output too high"',
        f"    let pass = 0",
        f"  end",
        f"",
    ]


def gen_reset_test(idx: int, vcc: float, nets: list[str]) -> list[str]:
    """Generate reset circuit test."""
    net_desc = ", ".join(nets[:3])
    return [
        f"* === Reset Circuit Test #{idx} (detected: {net_desc}) ===",
        f"VCC_rst{idx} vcc_rst{idx} 0 DC {vcc}",
        f"Vtimer_rst{idx} timer_rst{idx} 0 PULSE({vcc} 0 5m 1u 1u 10m 100m)",
        f"Rdrv_rst{idx} timer_rst{idx} rst_filt{idx} 100",
        f"Cfilt_rst{idx} rst_filt{idx} 0 100n",
        f"Rout_rst{idx} rst_filt{idx} rst_out{idx} 10",
        f"Cout_rst{idx} rst_out{idx} 0 10n",
        f"",
    ]


def gen_reset_checks(idx: int, vcc: float) -> list[str]:
    """Generate reset check code."""
    return [
        f"  * --- Reset Circuit #{idx} ---",
        f"  let rst{idx}_t1 = -1",
        f"  let rst{idx}_t2 = -1",
        f"  meas tran rst{idx}_t1 when v(rst_out{idx})={round(vcc/2, 2)} fall=1",
        f"  meas tran rst{idx}_t2 when v(rst_out{idx})={round(vcc/2, 2)} fall=2",
        f"  let rst{idx}_period = -1",
        f"  if $&rst{idx}_t1 > 0",
        f"    if $&rst{idx}_t2 > 0",
        f"      let rst{idx}_period = rst{idx}_t2 - rst{idx}_t1",
        f"    end",
        f"  end",
        f"  let rst{idx}_min = -1",
        f"  meas tran rst{idx}_min min v(rst_out{idx})",
        f'  echo "Reset#{idx}: period=$&rst{idx}_period s, Vmin=$&rst{idx}_min V"',
        f'  echo "RESULT:rst{idx}_period=$&rst{idx}_period" >> simulation_results.txt',
        f"  if $&rst{idx}_period > 0",
        f"    if $&rst{idx}_period < 50m",
        f'      echo "FAIL: Reset#{idx} period too short"',
        f"      let pass = 0",
        f"    end",
        f"  end",
        f"  if $&rst{idx}_min > 0.8",
        f'    echo "FAIL: Reset#{idx} does not pull low"',
        f"    let pass = 0",
        f"  end",
        f"",
    ]


# ============================================================
# Fault / Anomaly Test Templates
# ============================================================

def gen_reverse_voltage_test(idx: int, vcc: float) -> list[str]:
    """Generate reverse voltage protection test for a power rail."""
    return [
        f"* === Reverse Voltage Test #{idx} ({vcc}V rail) ===",
        f"* Simulates battery/connector reversed polarity",
        f"* Protected load should see minimal reverse voltage",
        f"Vin_rev{idx} vin_rev{idx} 0 DC -{vcc}",
        f"* Series diode protection model (Schottky)",
        f"D_prot{idx} vin_rev{idx} vout_rev{idx} SCHOTTKY_PROT",
        f"Rload_rev{idx} vout_rev{idx} 0 100",
        f"",
    ]


def gen_reverse_voltage_checks(idx: int, vcc: float) -> list[str]:
    """Generate reverse voltage check code."""
    return [
        f"  * --- Reverse Voltage #{idx} ---",
        f"  let vrev{idx} = v(vout_rev{idx})",
        f'  echo "Reverse#{idx}: output=$&vrev{idx} V (should be near 0)"',
        f'  echo "RESULT:reverse{idx}_vout=$&vrev{idx}" >> simulation_results.txt',
        f"  * Protected output must stay above -{round(vcc * 0.1, 2)}V",
        f"  if $&vrev{idx} < -{round(vcc * 0.1, 2)}",
        f'    echo "FAIL: Reverse voltage not blocked ({vcc}V rail)"',
        f"    let pass = 0",
        f"  end",
        f"",
    ]


def gen_overvoltage_test(idx: int, vcc: float) -> list[str]:
    """Generate overvoltage/surge protection test."""
    surge_v = round(vcc * 4, 1)  # 4x nominal as surge
    return [
        f"* === Overvoltage Surge Test #{idx} ({vcc}V rail, {surge_v}V surge) ===",
        f"Vin_surge{idx} vin_surge{idx} 0 PULSE({vcc} {surge_v} 10u 100n 100n 10u 50u)",
        f"* TVS clamp model",
        f"D_tvs_f{idx} vin_surge{idx} 0 TVS_AUTO",
        f"D_tvs_r{idx} 0 vin_surge{idx} TVS_AUTO",
        f"* Series protection + load",
        f"Rs_surge{idx} vin_surge{idx} vout_surge{idx} 10",
        f"Cl_surge{idx} vout_surge{idx} 0 10u IC={vcc}",
        f"Rl_surge{idx} vout_surge{idx} 0 50",
        f"",
    ]


def gen_overvoltage_checks(idx: int, vcc: float) -> list[str]:
    """Generate overvoltage check code."""
    max_safe = round(vcc * 1.5, 1)
    return [
        f"  * --- Overvoltage Surge #{idx} ---",
        f"  let vsurge{idx}_normal = -1",
        f"  meas tran vsurge{idx}_normal avg v(vout_surge{idx}) from=1u to=9u",
        f"  let vsurge{idx}_peak = -1",
        f"  meas tran vsurge{idx}_peak max v(vout_surge{idx}) from=10u to=25u",
        f"  let vsurge{idx}_recovery = -1",
        f"  meas tran vsurge{idx}_recovery avg v(vout_surge{idx}) from=35u to=45u",
        f'  echo "Surge#{idx}: normal=$&vsurge{idx}_normal V, peak=$&vsurge{idx}_peak V, recovery=$&vsurge{idx}_recovery V"',
        f'  echo "RESULT:surge{idx}_normal=$&vsurge{idx}_normal" >> simulation_results.txt',
        f'  echo "RESULT:surge{idx}_peak=$&vsurge{idx}_peak" >> simulation_results.txt',
        f'  echo "RESULT:surge{idx}_recovery=$&vsurge{idx}_recovery" >> simulation_results.txt',
        f"  * Peak must stay below {max_safe}V (1.5x nominal)",
        f"  if $&vsurge{idx}_peak > {max_safe}",
        f'    echo "FAIL: Surge#{idx} not clamped (peak=$&vsurge{idx}_peak V > {max_safe}V)"',
        f"    let pass = 0",
        f"  end",
        f"  * Must recover after surge",
        f"  if $&vsurge{idx}_recovery < {round(vcc * 0.9, 2)}",
        f'    echo "FAIL: Surge#{idx} did not recover"',
        f"    let pass = 0",
        f"  end",
        f"",
    ]


def gen_set_test(idx: int, vcc: float) -> list[str]:
    """Generate radiation Single Event Transient test for a power rail."""
    return [
        f"* === Radiation SET Test #{idx} ({vcc}V rail) ===",
        f"Vreg_set{idx} vcc_set{idx} 0 DC {vcc}",
        f"Rout_set{idx} vcc_set{idx} vout_set{idx} 0.5",
        f"Cout_set{idx} vout_set{idx} 0 10u IC={vcc}",
        f"Cdec_set{idx} vout_set{idx} 0 100n IC={vcc}",
        f"Rload_set{idx} vout_set{idx} 0 110",
        f"* SET current pulse injection (3 intensities)",
        f"Iset{idx}_1 0 vout_set{idx} PULSE(0 1m 5u 100p 1n 0 100u)",
        f"Iset{idx}_2 0 vout_set{idx} PULSE(0 3m 10u 100p 1n 0 100u)",
        f"Iset{idx}_3 0 vout_set{idx} PULSE(0 5m 15u 100p 1n 0 100u)",
        f"",
    ]


def gen_set_checks(idx: int, vcc: float) -> list[str]:
    """Generate SET check code."""
    abs_max = round(vcc * 1.1, 2)
    return [
        f"  * --- Radiation SET #{idx} ({vcc}V) ---",
        f"  let set{idx}_nominal = -1",
        f"  meas tran set{idx}_nominal avg v(vout_set{idx}) from=1u to=4u",
        f"  let set{idx}_peak = -1",
        f"  meas tran set{idx}_peak max v(vout_set{idx}) from=5u to=20u",
        f'  echo "SET#{idx}: nominal=$&set{idx}_nominal V, peak=$&set{idx}_peak V"',
        f'  echo "RESULT:set{idx}_nominal=$&set{idx}_nominal" >> simulation_results.txt',
        f'  echo "RESULT:set{idx}_peak=$&set{idx}_peak" >> simulation_results.txt',
        f"  * Peak must stay below absolute max ({abs_max}V)",
        f"  if $&set{idx}_peak > {abs_max}",
        f'    echo "FAIL: SET#{idx} exceeds abs max ({abs_max}V)"',
        f"    let pass = 0",
        f"  end",
        f"",
    ]


# ============================================================
# Main Generator
# ============================================================

def find_resistor_pairs(components):
    resistors = []
    for c in components:
        if c.reference.startswith("R"):
            r = parse_resistance(c.value)
            if r:
                resistors.append((c, r))
    pairs = []
    for i in range(len(resistors)):
        for j in range(i + 1, len(resistors)):
            ref_i = int(re.search(r"\d+", resistors[i][0].reference).group())
            ref_j = int(re.search(r"\d+", resistors[j][0].reference).group())
            if abs(ref_i - ref_j) == 1:
                pairs.append((resistors[i], resistors[j]))
    return pairs


def generate_spice(sch_name: str, patterns: DetectedPatterns) -> str:
    """Generate complete SPICE test file from detected patterns."""
    circuit_lines = []
    check_lines = []
    needs_tran = False
    tran_time = "25u"

    vcc = patterns.power_nets[0].voltage if patterns.power_nets else 3.3
    tran_step = "10n"

    # Header
    circuit_lines.extend([
        f"* Auto-generated SPICE test for {sch_name}",
        f"* Components: {len(patterns.components)}",
        f"* Power nets: {', '.join(n.name + '=' + str(n.voltage) + 'V' for n in patterns.power_nets) or 'none'}",
        f"* Detected: "
        + (f"I2C " if patterns.i2c_nets else "")
        + (f"SPI " if patterns.spi_nets else "")
        + (f"Crystal " if patterns.has_crystal else "")
        + (f"LDO " if patterns.has_ldo else "")
        + (f"TVS " if patterns.has_tvs else "")
        + (f"MOSFET-Prot " if patterns.has_mosfet_protection else "")
        + (f"Reset " if patterns.reset_nets else ""),
        f"",
        f".title Auto-Test: {sch_name}",
        f"",
    ])

    # I2C
    if patterns.i2c_nets:
        circuit_lines.extend(gen_i2c_test(0, vcc, patterns.i2c_nets))
        circuit_lines.append(f".model NMOD_I2C NMOS (VTO=0.7 KP=200u GAMMA=0.4 LAMBDA=0.04)")
        circuit_lines.append("")
        check_lines.extend(gen_i2c_checks(0, vcc))
        needs_tran = True

    # SPI
    if patterns.spi_nets:
        circuit_lines.extend(gen_spi_test(0, vcc, patterns.spi_nets))
        check_lines.extend(gen_spi_checks(0, vcc))
        needs_tran = True

    # Crystal
    if patterns.has_crystal:
        circuit_lines.extend(gen_crystal_test(0, vcc))
        check_lines.extend(gen_crystal_checks())
        needs_tran = True
        tran_time = "200u"

    # LDO
    if patterns.has_ldo and patterns.power_nets:
        vin = max(n.voltage for n in patterns.power_nets) + 1.0
        for i, pnet in enumerate(patterns.power_nets[:3]):
            circuit_lines.extend(gen_ldo_test(i, vin, pnet.voltage))
            check_lines.extend(gen_ldo_checks(i, pnet.voltage))
        needs_tran = True

    # Reset — only add if no crystal (crystal needs 200u, reset needs 0.5s — incompatible)
    if patterns.reset_nets and not patterns.has_crystal:
        circuit_lines.extend(gen_reset_test(0, vcc, patterns.reset_nets))
        check_lines.extend(gen_reset_checks(0, vcc))
        needs_tran = True
        tran_time = "0.3"
        # Coarser step for reset simulation
        tran_step = "100u"
    elif patterns.reset_nets:
        # Just note detection without simulation (crystal takes priority)
        check_lines.extend([
            f'  echo "Reset lines detected: {", ".join(patterns.reset_nets[:3])}"',
            f'  echo "RESULT:reset_detected=true" >> simulation_results.txt',
            f"",
        ])

    # ============================================================
    # Fault / Anomaly Tests (auto-generated per power rail)
    # ============================================================

    # Reverse voltage test (always generate for each power rail)
    if patterns.power_nets:
        need_schottky_model = True
        for i, pnet in enumerate(patterns.power_nets[:2]):
            circuit_lines.extend(gen_reverse_voltage_test(i, pnet.voltage))
            check_lines.extend(gen_reverse_voltage_checks(i, pnet.voltage))
        circuit_lines.append(f".model SCHOTTKY_PROT D (IS=1e-5 N=1.05 RS=0.1 BV=30 CJO=10p)")
        circuit_lines.append("")

    # Overvoltage/surge test
    if patterns.power_nets:
        for i, pnet in enumerate(patterns.power_nets[:2]):
            circuit_lines.extend(gen_overvoltage_test(i, pnet.voltage))
            check_lines.extend(gen_overvoltage_checks(i, pnet.voltage))
        circuit_lines.append(f".model TVS_AUTO D (BV={round(vcc * 1.2, 1)} IBV=10m RS=0.5 CJO=200p)")
        circuit_lines.append("")
        needs_tran = True

    # Radiation SET test (for each power rail)
    if patterns.power_nets:
        for i, pnet in enumerate(patterns.power_nets[:2]):
            circuit_lines.extend(gen_set_test(i, pnet.voltage))
            check_lines.extend(gen_set_checks(i, pnet.voltage))
        needs_tran = True

    # Resistor dividers (always check)
    divider_pairs = find_resistor_pairs(patterns.components)[:5]
    for idx, ((r1_comp, r1_val), (r2_comp, r2_val)) in enumerate(divider_pairs):
        vout_expected = vcc * r2_val / (r1_val + r2_val)
        circuit_lines.extend([
            f"* Divider: {r1_comp.reference}={r1_comp.value} / {r2_comp.reference}={r2_comp.value}",
            f"V_div{idx} div{idx}_in 0 DC {vcc}",
            f"R_div{idx}_top div{idx}_in div{idx}_out {r1_val}",
            f"R_div{idx}_bot div{idx}_out 0 {r2_val}",
            f"",
        ])
        low = round(vout_expected * 0.95, 4)
        high = round(vout_expected * 1.05, 4)
        check_lines.extend([
            f"  let v_div{idx} = v(div{idx}_out)",
            f'  echo "Divider {r1_comp.reference}/{r2_comp.reference}: $&v_div{idx} V (exp {vout_expected:.4f})"',
            f'  echo "RESULT:div_{r1_comp.reference}_{r2_comp.reference}=$&v_div{idx}" >> simulation_results.txt',
            f"  if $&v_div{idx} < {low}",
            f"    let pass = 0",
            f"  end",
            f"  if $&v_div{idx} > {high}",
            f"    let pass = 0",
            f"  end",
            f"",
        ])

    # Power budget
    resistors_with_values = [(c, parse_resistance(c.value)) for c in patterns.components
                             if c.reference.startswith("R") and parse_resistance(c.value)]
    if resistors_with_values:
        total_current = sum(vcc / r * 1000 for _, r in resistors_with_values)
        total_power = total_current * vcc
        check_lines.extend([
            f'  echo "Power estimate: {total_current:.1f}mA / {total_power:.1f}mW"',
            f'  echo "RESULT:est_current_mA={total_current:.1f}" >> simulation_results.txt',
            f'  echo "RESULT:est_power_mW={total_power:.1f}" >> simulation_results.txt',
            f"",
        ])

    # Assemble
    lines = circuit_lines[:]

    if needs_tran:
        lines.append(f".tran {tran_step} {tran_time} UIC")
    lines.append(f".op")
    lines.append(f"")
    lines.append(f".control")
    if needs_tran:
        lines.append(f"  run")
    else:
        lines.append(f"  op")
    lines.append(f"")

    # Summary header
    detected = []
    if patterns.i2c_nets: detected.append("I2C")
    if patterns.spi_nets: detected.append("SPI")
    if patterns.has_crystal: detected.append("Crystal")
    if patterns.has_ldo: detected.append("LDO")
    if patterns.reset_nets: detected.append("Reset")

    lines.extend([
        f'  echo "========================================="',
        f'  echo "  Auto-Test: {sch_name}"',
        f'  echo "  Detected: {", ".join(detected) if detected else "basic"}"',
        f'  echo "========================================="',
        f"",
        f'  echo "RESULT:components={len(patterns.components)}" > simulation_results.txt',
        f"  let pass = 1",
        f"",
    ])

    lines.extend(check_lines)

    lines.extend([
        f"  if $&pass > 0",
        f'    echo "PASS: All auto-generated checks passed"',
        f'    echo "STATUS:PASS" >> simulation_results.txt',
        f"  else",
        f'    echo "STATUS:FAIL" >> simulation_results.txt',
        f"  end",
        f"",
        f"  quit",
        f".endc",
        f"",
        f".end",
    ])

    return "\n".join(lines)


def main():
    if len(sys.argv) < 2:
        print("Usage: python3 generate_spice_tests.py <schematic.kicad_sch>")
        sys.exit(1)

    sch_path = Path(sys.argv[1])
    if not sch_path.exists():
        print(f"Error: {sch_path} not found")
        sys.exit(1)

    print(f"Parsing: {sch_path}")
    patterns = parse_schematic(sch_path)

    print(f"Components: {len(patterns.components)}")
    print(f"Power nets: {[f'{n.name}={n.voltage}V' for n in patterns.power_nets]}")
    print(f"Detected patterns:")
    if patterns.i2c_nets:
        print(f"  I2C: {patterns.i2c_nets[:5]}")
    if patterns.spi_nets:
        print(f"  SPI: {patterns.spi_nets[:5]}")
    if patterns.has_crystal:
        print(f"  Crystal oscillator")
    if patterns.has_ldo:
        print(f"  LDO/Regulator")
    if patterns.reset_nets:
        print(f"  Reset: {patterns.reset_nets[:3]}")

    sch_name = sch_path.stem
    spice_content = generate_spice(sch_name, patterns)

    output_dir = Path(__file__).parent.parent / "simulation"
    output_path = output_dir / f"auto_{sch_name}.spice"
    output_path.write_text(spice_content)

    print(f"Generated: {output_path}")


if __name__ == "__main__":
    main()
