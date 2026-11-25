function plot_wind(ac_data)
%PLOT_WIND Wind in use (pprz.wind: AIR_DATA in newer logs, WIND_INFO_RET in older ones): speed and the direction
%   it comes from, components and airspeed; newer logs add each wind source (WIND_RAW, when logged) and the source
%   in use (AIR_DATA wind_id).
    w = pprz.wind(ac_data);
    raw = struct();
    if pprz.has(ac_data, 'WIND_RAW'), raw = ac_data.WIND_RAW; end
    in_use = any(isfinite(w.id));
    axs = pprz.tiles(3 + in_use, 'Wind');
    plot(axs(1), w.t, w.speed, 'DisplayName', sprintf('Horizontal speed (%s)', w.source));
    if isfield(raw, 'sensor_id')
        for s = reshape(unique(double(raw.sensor_id(:))), 1, [])
            rows = double(raw.sensor_id(:)) == s;
            v = double(raw.wind(rows, :));
            plot(axs(1), double(raw.t(rows)), hypot(v(:, 1), v(:, 2)), ':', 'DisplayName', sprintf('Source %d (WIND_RAW)', s));
        end
    end
    ylabel(axs(1), 'Wind [m/s]');
    yyaxis(axs(1), 'right');
    plot(axs(1), w.t, w.from, '.', 'DisplayName', 'From');
    set(axs(1), 'YLim', [0 360], 'YTick', 0:90:360);
    ylabel(axs(1), 'From [deg]');
    yyaxis(axs(1), 'left');
    legend(axs(1), 'Location', 'best', 'Interpreter', 'none');
    pprz.series(axs(2), w.t, [w.north, w.east, w.up], {'North', 'East', 'Up'});
    ylabel(axs(2), 'Wind components [m/s]');
    if any(isfinite(w.airspeed) & w.airspeed ~= 0)  % not every wind estimator sets it
        plot(axs(3), w.t, w.airspeed, 'DisplayName', 'Airspeed (wind estimator)');
    end
    if pprz.has(ac_data, 'AIR_DATA')
        plot(axs(3), ac_data.AIR_DATA.t, ac_data.AIR_DATA.airspeed, 'DisplayName', 'Airspeed (AIR_DATA)');
    end
    ylabel(axs(3), 'Airspeed [m/s]');
    legend(axs(3), 'Location', 'best', 'Interpreter', 'none');
    if in_use
        stairs(axs(4), w.t, w.id, 'DisplayName', 'Wind source in use');
        ylabel(axs(4), 'Source (ABI id)');
    end
end
