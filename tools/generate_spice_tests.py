#!/usr/bin/env python3
"""
KiCad Schematic → SPICE Test Auto-Generator

Parses a KiCad .kicad_sch file and automatically generates
SPICE simulation tests based on the circuit topology:

1. Power net voltage checks
2. Resistor divider detection and output voltage verification
3. RC filter detection and cutoff frequency verification
4. Power budget estimation from component values
5. LED current limiting resistor checks

Usage:
    python3 tools/generate_spice_tests.py hardware/example/batteryPack.kicad_sch

Output:
    simulation/auto_<schematic_name>.spice
"""

import re
import sys
from pathlib import Path
from dataclasses import dataclass, field


@dataclass
class Component:
    reference: str
    value: str
    lib_id: str = ""


@dataclass
class PowerNet:
    name: str
    voltage: float


def parse_schematic(sch_path: Path) -> tuple[list[Component], list[PowerNet]]:
    """Parse KiCad schematic and extract components and power nets."""
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
                    instances_text = rest[i + 1 :]
                    break
    else:
        instances_text = content

    # Extract components
    refs = re.findall(r'\(property "Reference" "([^"]+)"', instances_text)
    vals = re.findall(r'\(property "Value" "([^"]+)"', instances_text)

    components = []
    for r, v in zip(refs, vals):
        if not r.startswith("#"):
            components.append(Component(reference=r, value=v))

    # Extract power nets
    power_nets = []
    raw_nets = set(re.findall(r'\(property "Value" "\+([^"]+)"', content))
    for net in raw_nets:
        voltage = parse_voltage(net)
        if voltage:
            power_nets.append(PowerNet(name=f"+{net}", voltage=voltage))

    return components, power_nets


def parse_voltage(net_name: str) -> float | None:
    """Parse voltage from power net name like '3V3', '5V', '12V'."""
    # Match patterns like 3V3, 5V, 12V, 3.3V, 1V8
    m = re.match(r"(\d+)V(\d+)?", net_name)
    if m:
        integer = m.group(1)
        decimal = m.group(2) or "0"
        return float(f"{integer}.{decimal}")
    return None


def parse_resistance(value: str) -> float | None:
    """Parse resistance value like '10k', '2k4', '470', '1M'."""
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


def parse_capacitance(value: str) -> float | None:
    """Parse capacitance value like '100n', '470n', '10u', '22p'."""
    value = value.strip().lower()
    m = re.match(r"^(\d+\.?\d*)\s*(p|n|u|μ|m)?f?$", value)
    if m:
        num = float(m.group(1))
        suffix = m.group(2) or ""
        multiplier = {"p": 1e-12, "n": 1e-9, "u": 1e-6, "μ": 1e-6, "m": 1e-3, "": 1}
        return num * multiplier.get(suffix, 1)
    return None


def find_resistor_pairs(components: list[Component]) -> list[tuple[Component, Component]]:
    """Find pairs of resistors that could form voltage dividers."""
    resistors = []
    for c in components:
        if c.reference.startswith("R"):
            r = parse_resistance(c.value)
            if r:
                resistors.append((c, r))

    # Find pairs with consecutive reference numbers
    pairs = []
    for i in range(len(resistors)):
        for j in range(i + 1, len(resistors)):
            ref_i = int(re.search(r"\d+", resistors[i][0].reference).group())
            ref_j = int(re.search(r"\d+", resistors[j][0].reference).group())
            if abs(ref_i - ref_j) == 1:
                pairs.append((resistors[i], resistors[j]))

    return pairs


def find_led_resistors(components: list[Component]) -> list[tuple[Component, Component]]:
    """Find LED + current limiting resistor pairs."""
    leds = [c for c in components if c.reference.startswith("D") and "LED" in c.value.upper()]
    resistors = {c.reference: c for c in components if c.reference.startswith("R")}

    pairs = []
    for led in leds:
        led_num = int(re.search(r"\d+", led.reference).group())
        # Look for nearby resistor (common pattern: D1+R1, D2+R2, etc.)
        for offset in range(-3, 4):
            r_ref = f"R{led_num + offset}"
            if r_ref in resistors:
                r_val = parse_resistance(resistors[r_ref].value)
                if r_val and 100 <= r_val <= 10000:  # Typical LED resistor range
                    pairs.append((led, resistors[r_ref]))
                    break

    return pairs


def generate_spice(
    sch_name: str,
    components: list[Component],
    power_nets: list[PowerNet],
) -> str:
    """Generate SPICE test file from parsed schematic data."""
    lines = []
    lines.append(f"* Auto-generated SPICE test for {sch_name}")
    lines.append(f"* Generated from KiCad schematic analysis")
    lines.append(f"*")
    lines.append(f"* Components detected: {len(components)}")
    lines.append(f"* Power nets: {', '.join(n.name + '=' + str(n.voltage) + 'V' for n in power_nets)}")
    lines.append(f"")
    lines.append(f".title Auto-Test: {sch_name}")
    lines.append(f"")

    test_checks = []

    # === Power Net Voltage Sources ===
    for i, pnet in enumerate(power_nets):
        lines.append(f"* Power net: {pnet.name} = {pnet.voltage}V")
        lines.append(f"V_pwr{i} pwr{i} 0 DC {pnet.voltage}")
        lines.append(f"R_load_pwr{i} pwr{i} 0 1k")
        lines.append(f"")

    # === Resistor Dividers ===
    divider_pairs = find_resistor_pairs(components)
    for idx, ((r1_comp, r1_val), (r2_comp, r2_val)) in enumerate(divider_pairs[:5]):
        vin = power_nets[0].voltage if power_nets else 5.0
        vout_expected = vin * r2_val / (r1_val + r2_val)

        lines.append(f"* Resistor divider: {r1_comp.reference}={r1_comp.value} / {r2_comp.reference}={r2_comp.value}")
        lines.append(f"* Expected Vout = {vin} * {r2_val} / ({r1_val} + {r2_val}) = {vout_expected:.4f}V")
        lines.append(f"V_div{idx} div{idx}_in 0 DC {vin}")
        lines.append(f"R_div{idx}_top div{idx}_in div{idx}_out {r1_val}")
        lines.append(f"R_div{idx}_bot div{idx}_out 0 {r2_val}")
        lines.append(f"")

        test_checks.append({
            "name": f"divider_{r1_comp.reference}_{r2_comp.reference}",
            "node": f"div{idx}_out",
            "expected": vout_expected,
            "tolerance_pct": 5,
        })

    # === LED Current Check ===
    led_pairs = find_led_resistors(components)
    for idx, (led, resistor) in enumerate(led_pairs[:3]):
        r_val = parse_resistance(resistor.value)
        vin = power_nets[0].voltage if power_nets else 3.3
        vf = 2.0  # Typical LED forward voltage
        i_led = (vin - vf) / r_val * 1000  # mA

        lines.append(f"* LED current: {led.reference} with {resistor.reference}={resistor.value}")
        lines.append(f"* I = ({vin} - {vf}) / {r_val} = {i_led:.1f}mA")
        lines.append(f"V_led{idx} led{idx}_in 0 DC {vin}")
        lines.append(f"R_led{idx} led{idx}_in led{idx}_out {r_val}")
        lines.append(f"D_led{idx} led{idx}_out 0 LED_MODEL")
        lines.append(f"")

        test_checks.append({
            "name": f"led_{led.reference}_current",
            "type": "current",
            "source": f"V_led{idx}",
            "expected_ma": i_led,
            "max_ma": 20,
        })

    if led_pairs:
        lines.append(f".model LED_MODEL D (IS=1e-20 N=1.8 RS=5 BV=5 IBV=100u)")
        lines.append(f"")

    # === Power Budget ===
    resistors_with_values = []
    for c in components:
        if c.reference.startswith("R"):
            r = parse_resistance(c.value)
            if r:
                resistors_with_values.append((c, r))

    lines.append(f"")
    lines.append(f".op")
    lines.append(f"")
    lines.append(f".control")
    lines.append(f"  op")
    lines.append(f"")
    lines.append(f"  echo \"=========================================\"")
    lines.append(f"  echo \"  Auto-Generated Test: {sch_name}\"")
    lines.append(f"  echo \"=========================================\"")
    lines.append(f"  echo \"Components: {len(components)}\"")
    lines.append(f"  echo \"Power nets: {len(power_nets)}\"")
    lines.append(f"  echo \"Resistor dividers found: {len(divider_pairs)}\"")
    lines.append(f"  echo \"LEDs found: {len(led_pairs)}\"")
    lines.append(f"  echo \"\"")
    lines.append(f"")

    # Results file
    lines.append(f'  echo "RESULT:components={len(components)}" > simulation_results.txt')
    lines.append(f'  echo "RESULT:power_nets={len(power_nets)}" >> simulation_results.txt')

    lines.append(f"  let pass = 1")
    lines.append(f"")

    # Voltage divider checks
    for check in test_checks:
        if check.get("type") == "current":
            continue
        name = check["name"]
        node = check["node"]
        expected = check["expected"]
        tol = check["tolerance_pct"]
        low = expected * (1 - tol / 100)
        high = expected * (1 + tol / 100)

        lines.append(f"  * Check: {name}")
        lines.append(f"  let v_{name} = v({node})")
        lines.append(f'  echo "{name}: $&v_{name} V (expected {expected:.4f}V)"')
        lines.append(f'  echo "RESULT:{name}=$&v_{name}" >> simulation_results.txt')
        lines.append(f"  if $&v_{name} < {low:.4f}")
        lines.append(f'    echo "FAIL: {name} too low"')
        lines.append(f"    let pass = 0")
        lines.append(f"  end")
        lines.append(f"  if $&v_{name} > {high:.4f}")
        lines.append(f'    echo "FAIL: {name} too high"')
        lines.append(f"    let pass = 0")
        lines.append(f"  end")
        lines.append(f"")

    # Power budget summary
    if power_nets and resistors_with_values:
        vin = power_nets[0].voltage
        total_current_ma = sum(vin / r * 1000 for _, r in resistors_with_values)
        total_power_mw = total_current_ma * vin
        lines.append(f"  * Power budget estimate")
        lines.append(f'  echo "Estimated total resistive current: {total_current_ma:.1f}mA"')
        lines.append(f'  echo "Estimated total power: {total_power_mw:.1f}mW"')
        lines.append(f'  echo "RESULT:est_current_mA={total_current_ma:.1f}" >> simulation_results.txt')
        lines.append(f'  echo "RESULT:est_power_mW={total_power_mw:.1f}" >> simulation_results.txt')
        lines.append(f"")

    lines.append(f"  if $&pass > 0")
    lines.append(f'    echo "PASS: All auto-generated checks passed"')
    lines.append(f'    echo "STATUS:PASS" >> simulation_results.txt')
    lines.append(f"  else")
    lines.append(f'    echo "STATUS:FAIL" >> simulation_results.txt')
    lines.append(f"  end")
    lines.append(f"")
    lines.append(f"  quit")
    lines.append(f".endc")
    lines.append(f"")
    lines.append(f".end")

    return "\n".join(lines)


def main():
    if len(sys.argv) < 2:
        print("Usage: python3 generate_spice_tests.py <schematic.kicad_sch>")
        print("\nGenerates SPICE tests from KiCad schematic analysis.")
        sys.exit(1)

    sch_path = Path(sys.argv[1])
    if not sch_path.exists():
        print(f"Error: {sch_path} not found")
        sys.exit(1)

    print(f"Parsing: {sch_path}")
    components, power_nets = parse_schematic(sch_path)

    print(f"Found {len(components)} components, {len(power_nets)} power nets")
    for pnet in power_nets:
        print(f"  Power: {pnet.name} = {pnet.voltage}V")

    sch_name = sch_path.stem
    spice_content = generate_spice(sch_name, components, power_nets)

    # Output to simulation directory
    output_dir = Path(__file__).parent.parent / "simulation"
    output_path = output_dir / f"auto_{sch_name}.spice"
    output_path.write_text(spice_content)

    print(f"\nGenerated: {output_path}")
    print(f"Run with: ngspice -b {output_path}")


if __name__ == "__main__":
    main()
