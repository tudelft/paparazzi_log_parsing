function plot_baro(ac_data)
%PLOT_BARO Barometers: static pressure and barometric altitude (AIR_DATA), the raw pressure per barometer
%   (BARO_RAW) and, in newer logs, which barometer and airspeed sensor AIR_DATA uses. Works for every version:
%   BARO_RAW has abs/diff in older logs and one message per sensor (sensor_id, pressure, raw, offset,
%   temperature) in newer ones; AIR_DATA has baro_id/airspeed_id only in newer ones. Panels without data are left out.
    a = struct();
    if pprz.has(ac_data, 'AIR_DATA')
        a = ac_data.AIR_DATA;
    end
    b = struct();
    if pprz.has(ac_data, 'BARO_RAW')
        b = ac_data.BARO_RAW;
    end
    per_sensor = isfield(b, 'sensor_id');
    panels = {};
    if isfield(a, 'pressure') || isfield(b, 'abs') || per_sensor, panels{end + 1} = 'pressure'; end
    if isfield(a, 'amsl_baro'), panels{end + 1} = 'altitude'; end
    if per_sensor && isfield(b, 'temperature'), panels{end + 1} = 'temperature'; end
    if isfield(a, 'baro_id') || isfield(a, 'airspeed_id'), panels{end + 1} = 'in_use'; end
    axs = pprz.tiles(max(1, numel(panels)), 'Barometers');
    for k = 1:numel(panels)
        ax = axs(k);
        switch panels{k}
            case 'pressure'
                if isfield(a, 'pressure')
                    plot(ax, a.t, double(a.pressure) / 100, 'DisplayName', 'AIR_DATA (in use)');
                end
                if per_sensor
                    for s = reshape(unique(double(b.sensor_id(:))), 1, [])
                        rows = double(b.sensor_id(:)) == s;
                        if isfield(b, 'pressure')
                            plot(ax, b.t(rows), double(b.pressure(rows)) / 100, 'DisplayName', sprintf('Baro %d', s));
                        end
                        if isfield(b, 'raw')
                            plot(ax, b.t(rows), double(b.raw(rows)) / 100, ':', ...
                                'DisplayName', sprintf('Baro %d uncalibrated', s));
                        end
                    end
                elseif isfield(b, 'abs')
                    plot(ax, b.t, double(b.abs) / 100, 'DisplayName', 'BARO_RAW');
                end
                ylabel(ax, 'Static pressure [hPa]');
                legend(ax, 'Location', 'eastoutside', 'Interpreter', 'none');
            case 'altitude'
                plot(ax, a.t, double(a.amsl_baro), 'DisplayName', 'Barometric altitude (AMSL)');
                ylabel(ax, 'Altitude [m]');
            case 'temperature'
                for s = reshape(unique(double(b.sensor_id(:))), 1, [])
                    rows = double(b.sensor_id(:)) == s;
                    plot(ax, b.t(rows), double(b.temperature(rows)), 'DisplayName', sprintf('Baro %d', s));
                end
                ylabel(ax, 'Sensor temperature [°C]');
                legend(ax, 'Location', 'eastoutside');
            case 'in_use'
                if isfield(a, 'baro_id')
                    stairs(ax, a.t, double(a.baro_id), 'DisplayName', 'Barometer');
                end
                if isfield(a, 'airspeed_id')
                    stairs(ax, a.t, double(a.airspeed_id), 'DisplayName', 'Airspeed sensor');
                end
                ylabel(ax, 'Sensor id in use (0: none)');
                legend(ax, 'Location', 'best');
        end
    end
end
