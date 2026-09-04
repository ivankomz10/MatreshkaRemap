"""Writes MatreshkaScreen.setting.

Generated rather than hand-written: five Loaders of boilerplate by hand is how
a wrong filename gets in and stays. Run it after changing what the node exposes
or what sits inside the macro, then copy_to_resolve.py to install.
"""
from pathlib import Path


# Every tool carries its own range, and on the Edit page the comp is asked for
# frames at the timeline's own numbering -- 01:00:00:00 is frame 86400. A tool
# whose range ends at 0 answers once and then answers nothing, which arrives as
# a black frame after the first. Setting it on the Loaders alone is not enough:
# the Transform, the switches and the warps each have one too.
LASTS = """
						GlobalIn = Input { Value = 0, },
						GlobalOut = Input { Value = 1000000, },"""


def loader(name, file, pos):
    return f'''				{name} = Loader {{
					Clips = {{
						Clip {{
							ID = "Clip1",
							Filename = "Macros:Matreshka/{file}",
							FormatID = "OpenEXRFormat",
							StartFrame = -1,
							LengthSetManually = true,
							TrimIn = 0, TrimOut = 0,
							ExtendFirst = 0, ExtendLast = 0,
							Loop = 0, AspectMode = 0, Depth = 5,
							TimeCode = 0, GlobalStart = 0, GlobalEnd = 0
						}}
					}},
					CtrlWZoom = false,
					NameSet = true,
					Inputs = {{{LASTS}
						Depth = Input {{ Value = 5, }},
						["Clip1.OpenEXRFormat.RedName"] = Input {{ Value = FuID {{ "R" }}, }},
						["Clip1.OpenEXRFormat.GreenName"] = Input {{ Value = FuID {{ "G" }}, }},
						["Clip1.OpenEXRFormat.BlueName"] = Input {{ Value = FuID {{ "B" }}, }},
						["Clip1.OpenEXRFormat.AlphaName"] = Input {{ Value = FuID {{ "A" }}, }}
					}},
					ViewInfo = OperatorInfo {{ Pos = {{ {pos[0]}, {pos[1]} }} }},
				}},
'''

def remap(name, source, stmap, pos, area=False):
    extra = LASTS
    if area:
        extra += """
						UseArea = Input { Value = 1, },
						AreaCentre = Input { Value = { 0.5, 0.5 }, },
						AreaHeight = Input { Value = 1, },
						AreaAspect = Input { Value = 0.847458, },"""
    return f'''				{name} = Fuse.MatreshkaRemap {{
					CtrlWZoom = false,
					NameSet = true,
					Inputs = {{
						Source = Input {{ SourceOp = "{source}", Source = "Output", }},
						STMap = Input {{ SourceOp = "{stmap}", Source = "Output", }},{extra}
					}},
					ViewInfo = OperatorInfo {{ Pos = {{ {pos[0]}, {pos[1]} }} }},
				}},
'''

# Exposed on the node, in the order they appear in the Inspector.
exposed = [
    ("MainInput1", "In", "Input", "Source", None),
    ("Mode", "PickMode", "Source", "Mode", 0),
    ("Resolution", "PickMap", "Source", "Resolution", 0),
    ("AreaCentre", "Flat", "AreaCentre", "Source area centre", None),
    ("AreaHeight", "Flat", "AreaHeight", "Source area height", None),
    ("AreaAspect", "Flat", "AreaAspect", "Source area aspect", None),
    ("Center", "In", "Center", "Position", None),
    ("UseSizeAndAspect", "In", "UseSizeAndAspect", "Size and aspect", None),
    ("Size", "In", "Size", "Size", None),
    ("Aspect", "In", "Aspect", "Aspect", None),
    ("XSize", "In", "XSize", "X size", None),
    ("YSize", "In", "YSize", "Y size", None),
    ("Angle", "In", "Angle", "Angle", None),
    ("FlipHoriz", "In", "FlipHoriz", "Flip horizontally", None),
    ("FlipVert", "In", "FlipVert", "Flip vertically", None),
]
inputs = ""
for key, op, src, label, default in exposed:
    inputs += f'''				{key} = InstanceInput {{
					SourceOp = "{op}",
					Source = "{src}",
					Name = "{label}",'''
    if default is not None:
        inputs += f'''
					Default = {default},'''
    inputs += '''
				},
'''

body = f'''{{
	Tools = ordered() {{
		MatreshkaScreen = MacroOperator {{
			CtrlWZoom = false,
			NameSet = true,
			Inputs = ordered() {{
{inputs}			}},
			Outputs = {{
				MainOutput1 = InstanceOutput {{
					SourceOp = "PickMode",
					Source = "Output",
				}}
			}},
			ViewInfo = GroupInfo {{ Pos = {{ 0, 0 }} }},
			Tools = ordered() {{
				In = Transform {{
					CtrlWZoom = false,
					NameSet = true,
					Inputs = {{{LASTS}
					}},
					ViewInfo = OperatorInfo {{ Pos = {{ 0, 0 }} }},
				}},
{loader("MapFull", "table_full_st.exr", (0, -148))}{loader("MapHalf", "table_half_st.exr", (0, -115))}{loader("MapQuarter", "table_quarter_st.exr", (0, -82))}{loader("MapViewer", "viewer_table_st.exr", (220, -148))}				PickMap = Switch {{
					CtrlWZoom = false,
					NameSet = true,
					Inputs = {{{LASTS}
						NumberOfInputs = Input {{ Value = 3, }},
						Name0 = Input {{ Value = "Full", }},
						Name1 = Input {{ Value = "Half", }},
						Name2 = Input {{ Value = "Quarter", }},
						Input0 = Input {{ SourceOp = "MapFull", Source = "Output", }},
						Input1 = Input {{ SourceOp = "MapHalf", Source = "Output", }},
						Input2 = Input {{ SourceOp = "MapQuarter", Source = "Output", }},
					}},
					ViewInfo = OperatorInfo {{ Pos = {{ 110, -115 }} }},
				}},
{remap("Flat", "In", "PickMap", (110, 0), area=True)}{remap("ViewerFull", "Flat", "MapViewer", (330, -60))}				PickMode = Switch {{
					CtrlWZoom = false,
					NameSet = true,
					Inputs = {{{LASTS}
						NumberOfInputs = Input {{ Value = 2, }},
						Name0 = Input {{ Value = "Flat", }},
						Name1 = Input {{ Value = "Viewer", }},
						Input0 = Input {{ SourceOp = "Flat", Source = "Output", }},
						Input1 = Input {{ SourceOp = "ViewerFull", Source = "Output", }},
					}},
					ViewInfo = OperatorInfo {{ Pos = {{ 550, 0 }} }},
				}},
			}},
		}}
	}},
	ActiveTool = "MatreshkaScreen"
}}
'''
# The same file in both places. Resolve reads Macros for the Fusion page and
# Templates/Edit/Effects for the Effects library, and a copy that drifts from
# the other is a difference nobody thinks to look for.
here = Path(__file__).resolve().parent
for target in (here / "Macros" / "MatreshkaScreen.setting",
               here / "Templates" / "Edit" / "Effects" / "MatreshkaScreen.setting"):
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(body, encoding="utf-8")
    print(f"written: {target.relative_to(here).as_posix()}  ({len(body)} bytes)")
