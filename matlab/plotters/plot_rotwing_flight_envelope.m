function plot_rotwing_flight_envelope(params, use_hysteresis)
%PLOT_ROTWING_FLIGHT_ENVELOPE Summary of this function goes here
%   Detailed explanation goes here

skew = 0:0.25:90;

min_airspeed = params.FW_QUAD_MIN_AIRSPEED * (skew - params.MIN_AIRSPEED_SLOPE_START_ANGLE) / (90 - params.MIN_AIRSPEED_SLOPE_START_ANGLE);
max_airspeed = params.QUAD_MAX_AIRSPEED + (params.FW_MAX_AIRSPEED - params.QUAD_MAX_AIRSPEED) * skew / params.FW_SKEW_ANGLE;

min_airspeed(min_airspeed < 0) = 0;
min_airspeed(min_airspeed > params.FW_MIN_AIRSPEED) = params.FW_MIN_AIRSPEED;

max_airspeed(max_airspeed < params.QUAD_MAX_AIRSPEED) = params.QUAD_MAX_AIRSPEED;
max_airspeed(max_airspeed > params.FW_MAX_AIRSPEED) = params.FW_MAX_AIRSPEED;

constants = [params.FW_MIN_AIRSPEED, params.FW_QUAD_MIN_AIRSPEED, params.FW_CRUISE_AIRSPEED, ...
    params.FW_MAX_AIRSPEED, params.QUAD_NOPUSH_AIRSPEED, params.QUAD_MAX_AIRSPEED, ...
    params.SKEW_START_AIRSPEED];

labels = {"FW\_MIN\_AIRSPEED", "FW\_QUAD\_MIN\_AIRSPEED", "FW\_CRUISE\_AIRSPEED", "FW\_MAX\_AIRSPEED", ...
    "QUAD\_NOPUSH\_AIRSPEED", "QUAD\_MAX\_AIRSPEED", "SKEW\_START\_AIRSPEED"};

skewing_x_vals = [0, 0, params.SKEW_ANGLE_STEP, params.SKEW_ANGLE_STEP, 90, 90];
skewing_y_vals = [0, params.SKEW_START_AIRSPEED, params.SKEW_START_AIRSPEED, params.QUAD_MAX_AIRSPEED, params.FW_MIN_AIRSPEED, params.FW_MAX_AIRSPEED];

hysteresis_x_vals = [params.SKEW_ANGLE_STEP, params.SKEW_ANGLE_STEP, 0];
hysteresis_y_vals = [params.SKEW_START_AIRSPEED, params.SKEW_START_AIRSPEED - params.HYSTERESIS_THRESHOLD, params.SKEW_START_AIRSPEED - params.HYSTERESIS_THRESHOLD];

figure;
plot(skew, min_airspeed, "Color", 'blue', LineWidth=2)
hold on
plot(skew, max_airspeed, "Color", 'red', LineWidth=2)
plot(skewing_x_vals, skewing_y_vals, "Color", 'black', 'LineWidth', 2)
if use_hysteresis
    plot(hysteresis_x_vals, hysteresis_y_vals, "Color", 'black', 'LineWidth', 2, 'LineStyle', '--')
end
hold on;
yline(constants, '--', labels)
legend(["min airspeed" "max airspeed" "Skewing path" "Hysteresis"])
xlabel("Skew angle [deg]")
ylabel("Airspeed [m/s]")


end

