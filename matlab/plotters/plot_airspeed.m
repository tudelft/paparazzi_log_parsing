function plot_airspeed(ac_data)
%PLOT_AIRSPEED Airspeed (AIR_DATA, and per sensor from AIRSPEED_RAW), differential pressure and angle of attack.
    raw = pprz.has(ac_data, 'AIRSPEED_RAW');
    aoa = pprz.has(ac_data, 'AOA');
    axs = pprz.tiles(1 + raw + aoa, 'Airspeed');
    if pprz.has(ac_data, 'AIR_DATA')
        plot(axs(1), ac_data.AIR_DATA.t, ac_data.AIR_DATA.airspeed, 'DisplayName', 'Airspeed (AIR_DATA)');
    end
    k = 1;
    if raw
        r = ac_data.AIRSPEED_RAW;
        k = k + 1;
        for s = reshape(unique(r.sensor_id), 1, [])
            rows = r.sensor_id == s;
            plot(axs(1), r.t(rows), r.airspeed(rows), 'DisplayName', sprintf('Sensor %d', s));
            plot(axs(k), r.t(rows), r.diffPress(rows), 'DisplayName', sprintf('Sensor %d', s));
        end
        ylabel(axs(k), 'Differential pressure [Pa]');
        legend(axs(k), 'Location', 'best');
    end
    ylabel(axs(1), 'Airspeed [m/s]');
    legend(axs(1), 'Location', 'best', 'Interpreter', 'none');
    if aoa
        plot(axs(end), ac_data.AOA.t, rad2deg(double(ac_data.AOA.angle)), 'DisplayName', 'Angle of attack');
        ylabel(axs(end), 'Angle of attack [deg]');
    end
end
