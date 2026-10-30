function plot_optitrack(ac_data)
%PLOT_OPTITRACK External pose (EXTERNAL_POSE_DOWN): NED position and the time between messages.
    m = ac_data.EXTERNAL_POSE_DOWN;
    axs = pprz.tiles(2, 'External pose');
    pprz.series(axs(1), m.t, [m.ned_x, m.ned_y, m.ned_z], {'North', 'East', 'Down'});
    ylabel(axs(1), 'Position [m]');
    plot(axs(2), m.t(2:end), 1000 * diff(m.t), '.', 'DisplayName', 'Interval');
    ylabel(axs(2), 'Message interval [ms]');
end
