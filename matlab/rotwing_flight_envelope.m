Rotwing_v3 = struct;
Rotwing_25kg = struct;

addpath("plotters/")

Rotwing_v3.FW_MIN_AIRSPEED = 17;
Rotwing_v3.FW_QUAD_MIN_AIRSPEED = 15;
Rotwing_v3.FW_CRUISE_AIRSPEED = 19;
Rotwing_v3.FW_MAX_AIRSPEED = 22;
Rotwing_v3.QUAD_NOPUSH_AIRSPEED = 5;
Rotwing_v3.QUAD_MAX_AIRSPEED = 12;
Rotwing_v3.SKEW_UP_AIRSPEED = 10;
Rotwing_v3.SKEW_DOWN_AIRSPEED = 8;
Rotwing_v3.SKEW_ANGLE_STEP = 55.0;
Rotwing_v3.MIN_AIRSPEED_SLOPE_START_ANGLE = 30;
Rotwing_v3.FW_SKEW_ANGLE = 80;

Rotwing_25kg.FW_MIN_AIRSPEED = 23;
Rotwing_25kg.FW_QUAD_MIN_AIRSPEED = 19;
Rotwing_25kg.FW_CRUISE_AIRSPEED = 25;
Rotwing_25kg.FW_MAX_AIRSPEED = 30;
Rotwing_25kg.QUAD_NOPUSH_AIRSPEED = 8;
Rotwing_25kg.QUAD_MAX_AIRSPEED = 15;
Rotwing_25kg.SKEW_UP_AIRSPEED = 13;
Rotwing_25kg.SKEW_DOWN_AIRSPEED = 11;
Rotwing_25kg.SKEW_ANGLE_STEP = 55.0;
Rotwing_25kg.MIN_AIRSPEED_SLOPE_START_ANGLE = 30;
Rotwing_25kg.FW_SKEW_ANGLE = 80;

plot_rotwing_flight_envelope(Rotwing_v3, "7kg");
plot_rotwing_flight_envelope(Rotwing_25kg, "25kg");
