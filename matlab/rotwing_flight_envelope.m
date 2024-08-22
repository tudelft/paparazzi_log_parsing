Rotwing_v3 = struct;
Rotwing_25kg = struct;

addpath("plotters\")

Rotwing_v3.FW_MIN_AIRSPEED = 17;
Rotwing_v3.FW_QUAD_MIN_AIRSPEED = 15;
Rotwing_v3.FW_CRUISE_AIRSPEED = 19;
Rotwing_v3.FW_MAX_AIRSPEED = 22;
Rotwing_v3.QUAD_NOPUSH_AIRSPEED = 5;
Rotwing_v3.QUAD_MAX_AIRSPEED = 15;
Rotwing_v3.SKEW_START_AIRSPEED = 10;
Rotwing_v3.SKEW_ANGLE_STEP = 55.0;
Rotwing_v3.MIN_AIRSPEED_SLOPE_START_ANGLE = 30;
Rotwing_v3.FW_SKEW_ANGLE = 85;
Rotwing_v3.HYSTERESIS_THRESHOLD = 2;

% Rotwing_25kg.FW_MIN_AIRSPEED = ;
% Rotwing_25kg.FW_QUAD_MIN_AIRSPEED = ;
% Rotwing_25kg.FW_CRUISE_AIRSPEED = ;
% Rotwing_25kg.FW_MAX_AIRSPEED = ;
% Rotwing_25kg.QUAD_NOPUSH_AIRSPEED = ;
% Rotwing_25kg.QUAD_MAX_AIRSPEED = ;
% Rotwing_25kg.SKEW_START_AIRSPEED = ;
% Rotwing_25kg.SKEW_ANGLE_STEP = 55;
% Rotwing_25kg.MIN_AIRSPEED_SLOPE_START_ANGLE = 30;
% Rotwing_25kg.FW_SKEW_ANGLE = 85;
% Rotwing_25kg.HYSTERESIS_THRESHOLD = ; 

use_hysteresis = true;

plot_rotwing_flight_envelope(Rotwing_v3, use_hysteresis);
% plot_rotwing_flight_envelope(Rotwing_25kg);
