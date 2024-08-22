Rotwing_v3 = struct;
Rotwing_25kg = struct;

addpath("plotters\")

Rotwing_v3.FW_MIN_AIRSPEED = 17;
Rotwing_v3.FW_QUAD_MIN_AIRSPEED = 15;
Rotwing_v3.FW_CRUISE_AIRSPEED = 19;
Rotwing_v3.FW_MAX_AIRSPEED = 22;
Rotwing_v3.QUAD_NOPUSH_AIRSPEED = 8;
Rotwing_v3.QUAD_MAX_AIRSPEED = 15;
Rotwing_v3.SKEW_START_AIRSPEED = 10;

% Rotwing_25kg.FW_MIN_AIRSPEED = ;
% Rotwing_25kg.FW_QUAD_MIN_AIRSPEED = ;
% Rotwing_25kg.FW_CRUISE_AIRSPEED = ;
% Rotwing_25kg.FW_MAX_AIRSPEED = ;
% Rotwing_25kg.QUAD_NOPUSH_AIRSPEED = ;
% Rotwing_25kg.QUAD_MAX_AIRSPEED = ;
% Rotwing_25kg.SKEW_START_AIRSPEED = ;

plot_rotwing_flight_envelope(Rotwing_v3);
% plot_rotwing_flight_envelope(Rotwing_25kg);
