function plot_guidance_indi_hybrid(ac_data)
%PLOT_GUIDANCE_INDI_HYBRID Guidance INDI: acceleration setpoint against the filtered NED acceleration, the
%   guidance output (roll and pitch increments, thrust increment), and speed setpoint against speed in the heading frame (forward, right, down).
    g = ac_data.GUIDANCE_INDI_HYBRID;
    has_fp = pprz.has(ac_data, 'ROTORCRAFT_FP');
    axs = pprz.tiles(2 + has_fp, 'Guidance INDI hybrid');
    colors = lines(3);
    axes_ = {'x', 'North'; 'y', 'East'; 'z', 'Down'};
    for c = 1:3
        plot(axs(1), g.t, g.(['accelned_filt_' axes_{c, 1}]), 'Color', colors(c, :), 'DisplayName', axes_{c, 2});
        plot(axs(1), g.t, g.(['sp_accel_' axes_{c, 1}]), '--', 'Color', colors(c, :), ...
            'DisplayName', [axes_{c, 2} ' setpoint']);
    end
    ylabel(axs(1), 'Acceleration [m/s²]');
    legend(axs(1), 'Location', 'eastoutside');
    pprz.series(axs(2), g.t, rad2deg([g.euler_cmd_x, g.euler_cmd_y]), {'Roll increment', 'Pitch increment'});
    ylabel(axs(2), 'Attitude increment [deg]');
    yyaxis(axs(2), 'right');
    plot(axs(2), g.t, g.euler_cmd_z, 'DisplayName', 'Thrust increment');
    ylabel(axs(2), 'Thrust increment');
    yyaxis(axs(2), 'left');
    if has_fp
        fp = ac_data.ROTORCRAFT_FP;
        psi = deg2rad(double(fp.psi));
        fwd = cos(psi) .* fp.vnorth + sin(psi) .* fp.veast;
        right = -sin(psi) .* fp.vnorth + cos(psi) .* fp.veast;
        psi_g = interp1(fp.t, unwrap(psi), g.t, 'nearest', 'extrap');
        sp_fwd = cos(psi_g) .* g.speed_sp_x + sin(psi_g) .* g.speed_sp_y;
        sp_right = -sin(psi_g) .* g.speed_sp_x + cos(psi_g) .* g.speed_sp_y;
        series = {fp.t, fwd, 'Forward'; fp.t, right, 'Right'; fp.t, -fp.vup, 'Down'};
        setpoints = {g.t, sp_fwd; g.t, sp_right; g.t, g.speed_sp_z};
        for c = 1:3
            plot(axs(3), series{c, 1}, series{c, 2}, 'Color', colors(c, :), 'DisplayName', series{c, 3});
            plot(axs(3), setpoints{c, 1}, setpoints{c, 2}, '--', 'Color', colors(c, :), ...
                'DisplayName', [series{c, 3} ' setpoint']);
        end
        ylabel(axs(3), 'Speed [m/s]');
        legend(axs(3), 'Location', 'eastoutside');
    end
end
