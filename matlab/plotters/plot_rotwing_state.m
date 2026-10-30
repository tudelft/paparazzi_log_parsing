function plot_rotwing_state(ac_data)
%PLOT_ROTWING_STATE Rotating wing state (ROTATING_WING_STATE): state, status flags, skew and airspeed limits.
    m = ac_data.ROTATING_WING_STATE;
    axs = pprz.tiles(4, 'Rotating wing state');
    pprz.series(axs(1), m.t, double([m.state, m.nav_state]), {'State', 'Navigation state'});
    ylabel(axs(1), 'State');
    flags = {'Skew valid', 'Hover motors enabled', 'Hover motors idle', 'Hover motors running', ...
        'Pusher running', 'Skew forced'};
    bits = double(m.status);
    for k = 1:numel(flags)
        stairs(axs(2), m.t, k + 0.8 * bitget(bits, k), 'LineWidth', 1.2, 'DisplayName', flags{k});
    end
    set(axs(2), 'YTick', 1:numel(flags), 'YTickLabel', flags, 'YLim', [0.5, numel(flags) + 1]);
    pprz.series(axs(3), m.t, [m.meas_skew_angle, m.sp_skew_angle], {'Measured', 'Setpoint'});
    ylabel(axs(3), 'Skew [deg]');
    if pprz.has(ac_data, 'AIR_DATA')
        plot(axs(4), ac_data.AIR_DATA.t, ac_data.AIR_DATA.airspeed, 'DisplayName', 'Measured');
    end
    pprz.series(axs(4), m.t, [m.nav_airspeed, m.min_airspeed, m.max_airspeed], {'Navigation', 'Minimum', 'Maximum'});
    ylabel(axs(4), 'Airspeed [m/s]');
    legend(axs(4), 'Location', 'eastoutside');
end
