function cycl_plot_guidance_indi_hybrid(ac_data)
    
    tiledlayout(3, 3, 'Padding', 'compact', 'TileSpacing', 'compact');

    ax1 = nexttile;
    hold on; grid on; zoom on;    
    h1 = plot(ac_data.GUIDANCE_INDI_HYBRID.timestamp,ac_data.GUIDANCE_INDI_HYBRID.sp_accel_x, LineWidth=1.5);
    h2 = plot(ac_data.GUIDANCE_INDI_HYBRID.timestamp,ac_data.GUIDANCE_INDI_HYBRID.accelned_filt_x, LineWidth=1.5);
    xlabel('time [s]');
    ylabel('a_N [m/s^2]');
    title('a_N');

    ax2 = nexttile;
    hold on; grid on; zoom on;    
    h3 = plot(ac_data.GUIDANCE_INDI_HYBRID.timestamp, ac_data.GUIDANCE_INDI_HYBRID.speed_sp_x, LineWidth=1.5);
    h4 = plot(ac_data.ROTORCRAFT_FP.timestamp, ac_data.ROTORCRAFT_FP.vnorth_alt, LineWidth=1.5);
    xlabel('time [s]');
    ylabel('v_N [m/s]');
    title('v_N');

    ax3 = nexttile;
    hold on; grid on; zoom on;    
    h5 = plot(ac_data.ROTORCRAFT_FP.timestamp, ac_data.ROTORCRAFT_FP.carrot_north_alt, LineWidth=1.5);
    h6 = plot(ac_data.ROTORCRAFT_FP.timestamp, ac_data.ROTORCRAFT_FP.north_alt, LineWidth=1.5);
    xlabel('time [s]');
    ylabel('x_N [m]');
    title('x_N');

    ax4 = nexttile;
    hold on; grid on; zoom on;    
    h7 = plot(ac_data.GUIDANCE_INDI_HYBRID.timestamp,ac_data.GUIDANCE_INDI_HYBRID.sp_accel_y, LineWidth=1.5);
    h8 = plot(ac_data.GUIDANCE_INDI_HYBRID.timestamp,ac_data.GUIDANCE_INDI_HYBRID.accelned_filt_y, LineWidth=1.5);
    xlabel('time [s]');
    ylabel('a_E [m/s^2]');
    title('a_E');

    ax5 = nexttile;
    hold on; grid on; zoom on;    
    h9 = plot(ac_data.GUIDANCE_INDI_HYBRID.timestamp, ac_data.GUIDANCE_INDI_HYBRID.speed_sp_y, LineWidth=1.5);
    h10 = plot(ac_data.ROTORCRAFT_FP.timestamp, ac_data.ROTORCRAFT_FP.veast_alt, LineWidth=1.5);
    xlabel('time [s]');
    ylabel('v_E [m/s]');
    title('v_E');

    ax6 = nexttile;
    hold on; grid on; zoom on;    
    h11 = plot(ac_data.ROTORCRAFT_FP.timestamp, ac_data.ROTORCRAFT_FP.carrot_east_alt, LineWidth=1.5);
    h12 = plot(ac_data.ROTORCRAFT_FP.timestamp, ac_data.ROTORCRAFT_FP.east_alt, LineWidth=1.5);
    xlabel('time [s]');
    ylabel('x_E [m]');
    title('x_E');

    ax7 = nexttile;
    hold on; grid on; zoom on;    
    h13 = plot(ac_data.GUIDANCE_INDI_HYBRID.timestamp,ac_data.GUIDANCE_INDI_HYBRID.sp_accel_z, LineWidth=1.5);
    h14 = plot(ac_data.GUIDANCE_INDI_HYBRID.timestamp,ac_data.GUIDANCE_INDI_HYBRID.accelned_filt_z, LineWidth=1.5);
    xlabel('time [s]');
    ylabel('a_D [m/s^2]');
    title('a_D');

    ax8 = nexttile;
    hold on; grid on; zoom on;    
    h15 = plot(ac_data.GUIDANCE_INDI_HYBRID.timestamp,ac_data.GUIDANCE_INDI_HYBRID.speed_sp_z, LineWidth=1.5);
    h16 = plot(ac_data.ROTORCRAFT_FP.timestamp,-ac_data.ROTORCRAFT_FP.vup_alt, LineWidth=1.5);
    xlabel('time [s]');
    ylabel('v_D [m/s]');
    title('v_D');

    ax9 = nexttile;
    hold on; grid on; zoom on;    
    h17 = plot(ac_data.ROTORCRAFT_FP.timestamp, -ac_data.ROTORCRAFT_FP.carrot_up_alt, LineWidth=1.5);
    h18 = plot(ac_data.ROTORCRAFT_FP.timestamp, -ac_data.ROTORCRAFT_FP.up_alt, LineWidth=1.5);
    xlabel('time [s]');
    ylabel('x_D [m]');
    title('x_D');

    % ax4 = nexttile;
    % hold on; grid on; zoom on;    
    % plot(ac_data.GUIDANCE_INDI_HYBRID.timestamp,ac_data.GUIDANCE_INDI_HYBRID.euler_cmd_x, LineWidth=1.5);
    % plot(ac_data.GUIDANCE_INDI_HYBRID.timestamp,ac_data.GUIDANCE_INDI_HYBRID.euler_cmd_y, LineWidth=1.5);
    % plot(ac_data.GUIDANCE_INDI_HYBRID.timestamp,ac_data.GUIDANCE_INDI_HYBRID.euler_cmd_z, LineWidth=1.5);
    % legend('roll cmd increment', 'pitch cmd increment', 'thrust cmd increment');
    % xlabel('time [s]');
    % ylabel('');
    % title('[phi, theta, thrust] cmd increments');

    % flight modes
    mode_values = ac_data.ROTORCRAFT_RADIO_CONTROL.mode;
    mode_timestamps = ac_data.ROTORCRAFT_RADIO_CONTROL.timestamp;
    cycl_draw_mode_transitions(mode_values, mode_timestamps, {ax1,ax2,ax3,ax4,ax5,ax6,ax7,ax8,ax9});
    legend(ax1, [h1,h2], {'a_N ref', 'a_N'});
    legend(ax2, [h3,h4], {'v_N ref', 'v_N'});
    legend(ax3, [h5,h6], {'x_N ref', 'x_N'});
    legend(ax4, [h7,h8], {'a_E ref', 'a_E'});
    legend(ax5, [h9,h10], {'v_E ref', 'v_E'});
    legend(ax6, [h11,h12], {'x_E ref', 'x_E'});
    legend(ax7, [h13,h14], {'a_D ref', 'a_D'});
    legend(ax8, [h15,h16], {'v_D ref', 'v_D'});
    legend(ax9, [h17,h18], {'x_D ref', 'x_D'});

    linkaxes([ax1,ax2,ax3,ax4,ax5,ax6,ax7,ax8,ax9],'x');
    
end