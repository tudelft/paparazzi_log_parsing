function cycl_plot_energy(ac_data)
    
    tot_motor_current = single(ac_data.SERIAL_ACT_T4_IN.motor_1_current_int)/100 + ...
                        single(ac_data.SERIAL_ACT_T4_IN.motor_2_current_int)/100;

    hold on; grid on; zoom on;
    yyaxis left;
    h1 = plot(ac_data.ENERGY.timestamp, ac_data.ENERGY.voltage, LineWidth=1.5);
    ylabel('voltage [V]');
    yyaxis right;
    h2 = plot(ac_data.SERIAL_ACT_T4_IN.timestamp, tot_motor_current, LineWidth=1.5);
    ylabel('current [A]');
    % title('');
end