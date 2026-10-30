function plot_wind(ac_data)
%PLOT_WIND Wind estimate (WIND_INFO_RET): speed and the direction it comes from, components, airspeed.
    w = ac_data.WIND_INFO_RET;
    axs = pprz.tiles(3, 'Wind');
    speed = hypot(w.east, w.north);
    from = mod(rad2deg(atan2(-w.east, -w.north)), 360);
    plot(axs(1), w.t, speed, 'DisplayName', 'Horizontal speed');
    ylabel(axs(1), 'Wind [m/s]');
    yyaxis(axs(1), 'right');
    plot(axs(1), w.t, from, '.', 'DisplayName', 'From');
    set(axs(1), 'YLim', [0 360], 'YTick', 0:90:360);
    ylabel(axs(1), 'From [deg]');
    yyaxis(axs(1), 'left');
    pprz.series(axs(2), w.t, [w.north, w.east, w.up], {'North', 'East', 'Up'});
    ylabel(axs(2), 'Wind components [m/s]');
    if any(w.airspeed ~= 0)  % not every wind estimator sets it
        plot(axs(3), w.t, w.airspeed, 'DisplayName', 'Airspeed (wind estimator)');
    end
    if pprz.has(ac_data, 'AIR_DATA')
        plot(axs(3), ac_data.AIR_DATA.t, ac_data.AIR_DATA.airspeed, 'DisplayName', 'Airspeed (AIR_DATA)');
    end
    ylabel(axs(3), 'Airspeed [m/s]');
    legend(axs(3), 'Location', 'best', 'Interpreter', 'none');
end
