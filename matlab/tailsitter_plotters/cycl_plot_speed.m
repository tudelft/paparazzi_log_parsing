function cycl_plot_speed(ac_data, order)

    tiledlayout(2, 1, 'Padding', 'compact', 'TileSpacing', 'compact');

    airspeed = ac_data.AIR_DATA.airspeed;
    airspeed(airspeed < 0) = 0;

    ax1 = nexttile;
    hold on; grid on; zoom on;
    h1 = plot(ac_data.AIR_DATA.timestamp, airspeed, LineWidth=1.5);
    h2 = yline(6, 'g--', 'LineWidth', 1.5);
    xlabel('Time [s]');
    ylabel('Airspeed [m/s]');
    title('Airspeed');

    ax2 = nexttile;
    hold on; grid on; zoom on;
    h3 = plot(ac_data.ROTORCRAFT_FP.timestamp, ac_data.ROTORCRAFT_FP.vup_alt, LineWidth=1.5);
    h4 = yline(-1.5, 'g--', 'LineWidth', 1.5);
    xlabel('Time [s]');
    ylabel('Vertical speed (-z) [m/s]');
    title('Vertical speed (-z)');

    % flight modes
    mode_values = ac_data.ROTORCRAFT_RADIO_CONTROL.mode;
    mode_timestamps = ac_data.ROTORCRAFT_RADIO_CONTROL.timestamp;
    cycl_draw_mode_transitions(mode_values, mode_timestamps, {ax1, ax2});
    legend(ax1, [h1, h2], {'Airspeed', '6 m/s'});
    legend(ax2, [h3, h4], {'Vertical speed (-z)', '-1.5 m/s'});

    linkaxes([ax1,ax2],'x');

end