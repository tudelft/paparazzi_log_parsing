function plot_hover_loop(ac_data)
%PLOT_HOVER_LOOP Hover loop position errors (HOVER_LOOP) and the thrust command (ROTORCRAFT_FP).
    m = ac_data.HOVER_LOOP;
    fp = pprz.has(ac_data, 'ROTORCRAFT_FP');
    axs = pprz.tiles(1 + fp, 'Hover loop');
    pprz.series(axs(1), m.t, [m.est_x - m.sp_x, m.est_y - m.sp_y], {'x error', 'y error'});
    ylabel(axs(1), 'Position error');
    if fp
        plot(axs(2), ac_data.ROTORCRAFT_FP.t, ac_data.ROTORCRAFT_FP.thrust, 'DisplayName', 'Thrust');
        ylabel(axs(2), 'Thrust [pprz]');
    end
end
