function plot_hybrid_guidance(ac_data)
%PLOT_HYBRID_GUIDANCE Hybrid guidance (HYBRID_GUIDANCE): speed setpoints, errors and wind estimate.
    m = ac_data.HYBRID_GUIDANCE;
    axs = pprz.tiles(3, 'Hybrid guidance');
    pprz.series(axs(1), m.t, [m.norm_ref_speed, m.speed_sp_x, m.speed_sp_y], {'Reference speed', 'x setpoint', 'y setpoint'});
    ylabel(axs(1), 'Speed [m/s]');
    pprz.series(axs(2), m.t, [m.pos_err_x, m.pos_err_y], {'x error', 'y error'});
    ylabel(axs(2), 'Position error [m]');
    yyaxis(axs(2), 'right');
    plot(axs(2), m.t, m.heading_diff, 'DisplayName', 'Heading error');
    ylabel(axs(2), 'Heading error [deg]');
    yyaxis(axs(2), 'left');
    pprz.series(axs(3), m.t, [m.wind_x, m.wind_y], {'x', 'y'});
    ylabel(axs(3), 'Wind [m/s]');
end
