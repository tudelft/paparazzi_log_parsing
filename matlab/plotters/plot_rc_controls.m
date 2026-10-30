function plot_rc_controls(ac_data)
%PLOT_RC_CONTROLS RC sticks, throttle, mode and kill [%] and RC status; PPM switches when PPM was logged.
    rc = ac_data.ROTORCRAFT_RADIO_CONTROL;
    ppm = pprz.has(ac_data, 'PPM');
    axs = pprz.tiles(3 + ppm, 'RC controls');
    pct = @(v) double(v) / 96;  % pprz units (±9600) to %
    pprz.series(axs(1), rc.t, [pct(rc.roll), pct(rc.pitch), pct(rc.yaw)], {'Roll', 'Pitch', 'Yaw'});
    ylabel(axs(1), 'Sticks [%]');
    pprz.series(axs(2), rc.t, [pct(rc.throttle), pct(rc.mode), pct(rc.kill)], {'Throttle', 'Mode', 'Kill'});
    ylabel(axs(2), 'Throttle, mode, kill [%]');
    stairs(axs(3), rc.t, double(rc.status), 'LineWidth', 1.2, 'DisplayName', 'RC status');
    set(axs(3), 'YTick', [0 1 2], 'YTickLabel', {'OK', 'Lost', 'Really lost'}, 'YLim', [-0.2 2.2]);
    ylabel(axs(3), 'RC status');
    if ppm
        p = ac_data.PPM;
        channels = size(p.values, 2);
        names = arrayfun(@(k) sprintf('Channel %d', k), 1:channels, 'UniformOutput', false);
        pprz.series(axs(4), p.t, double(p.values), names);
        ylabel(axs(4), 'PPM [µs]');
    end
end
