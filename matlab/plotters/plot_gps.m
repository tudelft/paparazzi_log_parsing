function plot_gps(ac_data)
%PLOT_GPS GPS fix and satellites, accuracies and PDOP (GPS_INT).
    g = ac_data.GPS_INT;
    axs = pprz.tiles(3, 'GPS');
    stairs(axs(1), g.t, double(g.numsv), 'DisplayName', 'Satellites');
    stairs(axs(1), g.t, double(g.fix), 'DisplayName', 'Fix (3 = 3-D)');
    ylabel(axs(1), 'Satellites, fix');
    legend(axs(1), 'Location', 'best');
    pprz.series(axs(2), g.t, [double(g.pacc) / 100, double(g.sacc) / 100], {'Position [m]', 'Speed [m/s]'});
    ylabel(axs(2), 'Accuracy');
    plot(axs(3), g.t, double(g.pdop) / 100, 'DisplayName', 'PDOP');
    ylabel(axs(3), 'PDOP');
end
