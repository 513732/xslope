XSLOPE seepage tools
====================
Developed against xslope[gui,fem] 1.0.9. Keep all files in this directory together.
Start_Budget_Studio.cmd launches the existing per-user xslope-venv on Windows.
Water budget > Build from saved workbook builds the offline mesh and results viewer.
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
