function plot_rotorcraft_cmd(ac_data)
%PLOT_ROTORCRAFT_CMD Roll, pitch, yaw and thrust commands (ROTORCRAFT_CMD, pprz units).
    m = ac_data.ROTORCRAFT_CMD;
    axs = pprz.tiles(2, 'Rotorcraft commands');
    pprz.series(axs(1), m.t, [m.cmd_roll, m.cmd_pitch, m.cmd_yaw], {'Roll', 'Pitch', 'Yaw'});
    ylabel(axs(1), 'Command [pprz]');
    plot(axs(2), m.t, double(m.cmd_thrust), 'DisplayName', 'Thrust');
    ylabel(axs(2), 'Thrust [pprz]');
end
