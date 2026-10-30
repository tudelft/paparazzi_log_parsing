function plot_lidar(ac_data)
%PLOT_LIDAR Lidar distance and status (LIDAR).
    m = ac_data.LIDAR;
    st = isfield(m, 'status');
    axs = pprz.tiles(1 + st, 'Lidar');
    plot(axs(1), m.t, m.distance, 'DisplayName', 'Distance');
    ylabel(axs(1), 'Distance [m]');
    if pprz.has(ac_data, 'ROTORCRAFT_FP')
        plot(axs(1), ac_data.ROTORCRAFT_FP.t, ac_data.ROTORCRAFT_FP.up, '--', 'DisplayName', 'Height (estimate)');
        legend(axs(1), 'Location', 'best');
    end
    if st
        stairs(axs(2), m.t, double(m.status), 'DisplayName', 'Status');
        ylabel(axs(2), 'Status');
    end
end
