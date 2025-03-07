function cycl_plot_3Dpos(ac_data, order)

    % hold on; grid on; zoom on;    
    % h1 = plot(ac_data.GUIDANCE_INDI_HYBRID.timestamp,ac_data.GUIDANCE_INDI_HYBRID.sp_accel_x, LineWidth=1.5);
    % h2 = plot(ac_data.GUIDANCE_INDI_HYBRID.timestamp,ac_data.GUIDANCE_INDI_HYBRID.accelned_filt_x, LineWidth=1.5);
    % xlabel('time [s]');
    % ylabel('a_N [m/s^2]');
    % title('a_N');

    north = ac_data.ROTORCRAFT_FP.north_alt;
    east = ac_data.ROTORCRAFT_FP.east_alt;
    up = ac_data.ROTORCRAFT_FP.up_alt;
    
    grid on;
    plot3(north, east, up, 'b', 'LineWidth', 1.5);
    xlabel('North (m)');
    ylabel('East (m)');
    zlabel('Altitude (m)');
    title('Cyclone2 3D Trajectory');
    legend('Trajectory');
    view(3);

    set(gca, 'YDir', 'reverse');

    % % flight modes
    % mode_values = ac_data.ROTORCRAFT_RADIO_CONTROL.mode;
    % mode_timestamps = ac_data.ROTORCRAFT_RADIO_CONTROL.timestamp;
    % cycl_draw_mode_transitions(mode_values, mode_timestamps, {ax1, ax2, ax3});
    % legend(ax1, [h1, h2], {'Theta', 'Theta Ref'});

end