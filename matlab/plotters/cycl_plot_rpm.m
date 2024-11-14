function cycl_plot_rpm(ac_data, order)

    rpm1 = ac_data.SERIAL_ACT_T4_IN.motor_1_rpm;
    rpm2 = ac_data.SERIAL_ACT_T4_IN.motor_2_rpm;

    tiledlayout(2, 1, 'Padding', 'compact', 'TileSpacing', 'compact');

    % First subplot for RPM
    ax1 = nexttile;
    hold on; zoom on;
    h1 = plot(ac_data.SERIAL_ACT_T4_IN.timestamp, rpm1, 'LineWidth', 1.5);
    h2 = plot(ac_data.SERIAL_ACT_T4_IN.timestamp, rpm2, 'LineWidth', 1.5);
    xlabel('Time [s]');
    ylabel('Prop speed [rpm]');
    title('RPM');
    grid on;

    % Second subplot for DShot commands
    ax2 = nexttile;
    hold on; zoom on;
    h3 = plot(ac_data.SERIAL_ACT_T4_OUT.timestamp, ac_data.SERIAL_ACT_T4_OUT.motor_1_dshot_cmd, 'LineWidth', 1.5);
    h4 = plot(ac_data.SERIAL_ACT_T4_OUT.timestamp, ac_data.SERIAL_ACT_T4_OUT.motor_2_dshot_cmd, 'LineWidth', 1.5);
    h5 = plot(ac_data.ROTORCRAFT_RADIO_CONTROL.timestamp, ac_data.ROTORCRAFT_RADIO_CONTROL.throttle, 'LineWidth', 1.5);
    xlabel('Time [s]');
    ylabel('DShot Command');
    title('DShot Command');
    grid on;

    linkaxes([ax1,ax2],'x');

    % background color fill for various flight modes
    mode_values = ac_data.ROTORCRAFT_RADIO_CONTROL.mode;
    mode_timestamps = ac_data.ROTORCRAFT_RADIO_CONTROL.timestamp;
    cycl_fill_mode_regions(mode_values, mode_timestamps, {ax1, ax2});
    legend(ax1, [h1, h2], {'RPM 1', 'RPM 2'});
    legend(ax2, [h3, h4, h5], {'DShot Cmd 1', 'DShot Cmd 2', 'Throttle'});
    
end