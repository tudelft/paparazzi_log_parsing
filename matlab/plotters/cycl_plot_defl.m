function cycl_plot_defl(ac_data, order)

    pos1 = double(ac_data.SERIAL_ACT_T4_IN.rotor_1_az_angle)/100;
    pos2 = double(ac_data.SERIAL_ACT_T4_IN.rotor_2_az_angle)/100;
    cmd_servo1 = double(ac_data.SERIAL_ACT_T4_OUT.rotor_1_az_angle_cmd)/100;
    cmd_servo2 = double(ac_data.SERIAL_ACT_T4_OUT.rotor_2_az_angle_cmd)/100;

    ax = axes;

    hold on; zoom on;
    h1 = plot(ac_data.SERIAL_ACT_T4_IN.timestamp, pos1, 'Color', 'm', 'LineWidth', 1.2);
    h2 = plot(ac_data.SERIAL_ACT_T4_IN.timestamp, pos2, 'Color', 'r', 'LineWidth', 1.2);
    h3 = plot(ac_data.SERIAL_ACT_T4_OUT.timestamp, cmd_servo1, ':', 'Color', 'b');
    h4 = plot(ac_data.SERIAL_ACT_T4_OUT.timestamp, cmd_servo2, ':', 'Color', 'b');
    xlabel('Time [s]');
    ylabel('Flap deflection [deg]');
    title('Flap deflection');

    % background color fill for various flight modes
    mode_values = ac_data.ROTORCRAFT_RADIO_CONTROL.mode;
    mode_timestamps = ac_data.ROTORCRAFT_RADIO_CONTROL.timestamp;
    cycl_fill_mode_regions(mode_values, mode_timestamps, {ax});
    legend(ax, [h1, h2, h3, h4], {'Flap 1', 'Flap 2', 'Flap 1 cmd', 'Flap 2 cmd'});
    
end