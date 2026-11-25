XSLOPE seepage tools
====================
Developed against xslope[gui,fem] 1.0.9. Keep all files in this directory together.
Windows setup
-------------
Extract the download and keep the entire seepage_tools directory together.
With 64-bit Python 3.12 installed, run Install_Budget_Studio.cmd. The installer
creates or uses %LOCALAPPDATA%\xslope-venv and checks Qt, WebEngine, netCDF4,
and FFmpeg. It does not require administrator rights or change system PATH.
Run Start_Budget_Studio.cmd from this directory to launch the enhanced Studio.
A shortcut to the stock XSLOPE application opens the stock Studio.
Water budget > Build for open workbook or Build from saved workbook opens the
results in Studio's Water budget tab. Export HTML saves a standalone offline
copy. The selection and CSV tools work in both views. For a transient run, use
Play drawdown or the time control to inspect the head or pore-pressure contours
and the predicted flows together. The blue isoline marks zero pore pressure.
Turn on Label contours to label the section automatically during playback and
video export. While paused, click a line to add its value. Manual labels are kept for
the displayed frame and contour variable while the viewer is open. Clear labels
removes the visible labels for that frame. Flow velocity arrows show the nodal
Darcy velocity, with a common speed reference across frames. Rebuild earlier
reports to include velocity data. The budget video includes visible overlays.
Save budget video exports the section and the selected nodes' flow chart together.
Between saved section frames, the section shows the most recent saved field.
Keep the workbook and its _mesh.json together outside the public repository.

Water budget > UKCP18 surface water balance > New from workbook creates a local
project configuration. Enter British National Grid easting and northing in metres.
Set pit plan areas (m2), representative lengths (m), boundary labels and recharge
ranges in local section coordinates. Check the default soil settings for the site.
Adjust start_dates, the antecedent spinup and the duration in 360-day model days.
For transient analysis the duration must match the existing workbook schedule.
The excavation and groundwater boundary schedule comes from that workbook.
Use one or more member IDs, or the string "all". Add conductivity cases using
material names as keys in K_multiplier; multipliers apply to both conductivities.

Online rainfall uses the Met Office WPS daily regional point product. Enter the
UKCP key in the masked field on your own computer. Save the project beside
a PET directory containing UKCEH Hydro-PE UKCP18 RCM PET files, organised
in member subdirectories (e.g. PET/01) and covering spin-up and construction.
Dataset: https://doi.org/10.5285/eb5d9dc4-13bb-44c7-9bf8-c5980fcf52a4
Run Install_Climate_Reader.cmd once to install netCDF4 in the virtual environment.
Local rain_folder or paired_csv paths can be used instead of an online request.
Paired CSV headers: member,date,cell_e,cell_n,rain_mm,pet_mm. Use one row per
member per 360-day date; units are mm/day and BNG metres. Use forward slashes
in Windows paths entered in JSON. Keep credentials out of all project files.

The daily soil store limits gross infiltration by the configured daily cap,
deducts actual ET up to available water, and releases overflow as drainage.
The finite-element solve receives prescribed drainage over the horizontal
projection of the selected upper edges. Edges touching existing BC nodes
are excluded. Check recharge_nodes.csv for the applied footprint. The method
does not switch to a ponded head at saturation. Review surface pressures.
exposed_area_fraction is a piecewise linear schedule [elapsed day, area fraction].
The default uses the full footprint from day zero. Pumping combines gross external
outflow at selected pit nodes, integrated over representative length, and direct
rainfall over exposed plan area. Routed runoff, pump controls, and sump storage
are outside this calculation. Steady cases use mean drainage and constant
groundwater flow. The soil spin-up is independent of the initial groundwater solve.

Each completed case writes an offline results HTML, daily budgets and nodal
recharge weights. The batch index and scenario_summary.csv compare cases.
Review solver warnings, surface pressures, mass balance and applied areas
before using rates for design. Live authenticated download and Windows Qt
operation of the climate extension require local verification.

Studio seepage result plots
---------------------------
Label contour enables click-to-label. Click close to a contour in the steady
or transient result plot. Labels use the active variable's units when declared
and stay with the current result variable and frame while the project is open.
Clear labels clears the active plot's labels.

Vertical scale changes the vertical-to-horizontal scale (1 = equal axis scales).
Save… exports the current figure, including its contour labels and vertical scale.
The transient tab's Save video… exports all saved frames as an H.264 MP4 at the
selected playback speed, using the current plot settings and vertical scale.
Frame times are the simulation times. Video playback advances by one saved frame
at a time, regardless of how far apart the simulation times are.

MP4 export requires imageio-ffmpeg in the same environment:
  "%LOCALAPPDATA%\xslope-venv\Scripts\python.exe" -m pip install imageio-ffmpeg
