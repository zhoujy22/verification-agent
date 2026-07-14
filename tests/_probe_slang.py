"""Probe pyslang 11.0.0 API + reproduce the slang_parser fallback path.

Run inside the docker image:
    python3 tests/_probe_slang.py
"""
from __future__ import annotations
import traceback
from pyslang.syntax import SyntaxTree
from pyslang.ast import Compilation

print("=" * 70)
print("PART 1: API introspection — what is root.definitions, does it have .body?")
print("=" * 70)

src = """
module dut #(parameter W=8)(input clk, input [W-1:0] d, output [W-1:0] q);
  assign q = d;
endmodule
module wrapper(input clk, input [7:0] d, output [7:0] q);
  dut #(.W(8)) u (.clk(clk), .d(d), .q(q));
endmodule
"""
tree = SyntaxTree.fromText(src, "probe.sv")
comp = Compilation()
comp.addSyntaxTree(tree)
root = comp.getRoot()

print("root type:", type(root).__name__)
print("has .definitions attr:", hasattr(root, "definitions"))

defs = getattr(root, "definitions", None)
print("definitions is None?", defs is None)
print("RootSymbol public attrs:", [a for a in dir(root) if not a.startswith("_")])
if defs is not None:
    for d in defs:
        print(f"  def name={getattr(d,'name','?')!r} type={type(d).__name__}")
        print(f"      has .body?     {hasattr(d, 'body')}")
        print(f"      has .portList? {hasattr(d, 'portList')}")
        print(f"      has .ports?    {hasattr(d, 'ports')}")
        attrs = [a for a in dir(d) if not a.startswith("_") and "port" in a.lower()]
        print(f"      port-ish attrs: {attrs}")

# Alternate entry points the new code might have intended
for meth in ("getDefinitions", "getAllInstanceBodies", "compilation"):
    print(f"root.{meth} exists?", hasattr(root, meth))

print()
print("top-level instances in root:")
for s in root:
    print(f"  inst name={getattr(s,'name','?')!r} type={type(s).__name__} "
          f"has .body={hasattr(s,'body')}")

print()
print("=" * 70)
print("PART 2: what happens if we set inst = Definition and do inst.body?")
print("=" * 70)
dut_def = next((d for d in (defs or []) if getattr(d, "name", "") == "dut"), None)
if dut_def is None:
    print("(no Definition objects available in this pyslang — defs is None/empty)")
else:
    print("dut_def type:", type(dut_def).__name__)
    try:
        body = dut_def.body
        print("  dut_def.body -> OK, type:", type(body).__name__,
              "has portList:", hasattr(body, "portList"))
    except AttributeError as e:
        print("  *** dut_def.body raised AttributeError:", e)

print()
print("=" * 70)
print("PART 3: drive slang_parser.parse() directly — the fallback path")
print("=" * 70)
import tempfile, os, sys
sys.path.insert(0, "/work")
from verif_agent.rtl_parser.slang_parser import parse, SlangParseError

with tempfile.TemporaryDirectory() as td:
    p = os.path.join(td, "wrap.v")
    with open(p, "w") as f:
        f.write(src)
    # top="dut" but dut is instantiated by wrapper -> NOT a top instance -> fallback
    print("parse(['wrap.v'], top='dut')  [forces the Definition fallback]")
    try:
        info = parse([p], "dut")
        print("  -> returned ports:", [pp["name"] for pp in info.ports])
        print("  -> n_ports:", len(info.ports))
    except SlangParseError as e:
        print("  -> SlangParseError:", e)
    except Exception as e:
        print("  -> *** UNEXPECTED", type(e).__name__, ":", e)
        traceback.print_exc()

print()
print("=" * 70)
print("PART 4: real case1.v (normal path — top IS a top instance)")
print("=" * 70)
c1 = "/work/public/A2-verification/testcases/A2_public_dataset/case1/rtl/case1.v"
if os.path.exists(c1):
    try:
        info = parse([c1], "axi_adapter_rd")
        print("  -> n_ports:", len(info.ports))
        print("  -> first 6 ports:", [pp["name"] for pp in info.ports[:6]])
    except Exception as e:
        print("  ->", type(e).__name__, ":", e)
else:
    print("  (case1.v not found)")
