function plot_rpm(ac_data, order)

    rpm1 = ac_data.SERIAL_ACT_T4_IN.motor_1_rpm;
    rpm2 = ac_data.SERIAL_ACT_T4_IN.motor_2_rpm;

    % % fix am32??? issues, or teensy???
    % for i = 2:length(rpm1)
    %     if rpm1(i) <= 0 || rpm1(i) > 12000
    %         rpm1(i) = rpm1(i - 1);
    %     end
    % end
    % 
    % for i = 2:length(rpm2)
    %     if rpm2(i) <= 0 || rpm2(i) > 12000
    %         rpm2(i) = rpm2(i - 1);
    %     end
    % end

    tiledlayout(2, 1, 'Padding', 'compact', 'TileSpacing', 'compact');

    ax1 = nexttile;
    hold on; zoom on;
    plot(ac_data.SERIAL_ACT_T4_IN.timestamp, rpm1);
    plot(ac_data.SERIAL_ACT_T4_IN.timestamp, rpm2);
    legend('rpm1', 'rpm2');
    xlabel('time(s)');
    ylabel('rpm');
    title('rpm');
    hold off;
    
    ax2 = nexttile;
    hold on; zoom on;
    plot(ac_data.SERIAL_ACT_T4_OUT.timestamp, ac_data.SERIAL_ACT_T4_OUT.motor_1_dshot_cmd);
    plot(ac_data.SERIAL_ACT_T4_OUT.timestamp, ac_data.SERIAL_ACT_T4_OUT.motor_2_dshot_cmd);
    plot(ac_data.ROTORCRAFT_RADIO_CONTROL.timestamp, ac_data.ROTORCRAFT_RADIO_CONTROL.throttle);
    legend('dshot cmd1', 'dshot cmd2', 'throttle');
    xlabel('time(s)');
    ylabel('dshot cmd');
    title('dshot cmd');
    hold off;

    linkaxes([ax1,ax2],'x')

end