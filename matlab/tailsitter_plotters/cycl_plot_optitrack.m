function cycl_plot_optitrack(ac_data)
    
    tiledlayout(4, 1, 'Padding', 'compact', 'TileSpacing', 'compact');

    diff_timestamps = diff(ac_data.EXTERNAL_POSE_DOWN.timestamp); 
    diff_timestamps(end+1) = diff_timestamps(end);

    ax1 = nexttile;
    hold on; grid on; zoom on;    
    h1 = plot(ac_data.EXTERNAL_POSE_DOWN.timestamp,ac_data.EXTERNAL_POSE_DOWN.ned_x, LineWidth=1.5);
    xlabel('time [s]');
    ylabel('ned x [m]');
    title('ned x');

    ax2 = nexttile;
    hold on; grid on; zoom on;    
    h2 = plot(ac_data.EXTERNAL_POSE_DOWN.timestamp,ac_data.EXTERNAL_POSE_DOWN.ned_y, LineWidth=1.5);
    xlabel('time [s]');
    ylabel('ned y [m]');
    title('ned y');

    ax3 = nexttile;
    hold on; grid on; zoom on;    
    h3 = plot(ac_data.EXTERNAL_POSE_DOWN.timestamp,ac_data.EXTERNAL_POSE_DOWN.ned_z, LineWidth=1.5);
    xlabel('time [s]');
    ylabel('ned z [m]');
    title('ned z');

    ax4 = nexttile;
    hold on; grid on; zoom on;    
    h4 = plot(ac_data.EXTERNAL_POSE_DOWN.timestamp,diff_timestamps, LineWidth=1.5);
    xlabel('time [s]');
    ylabel('diff timestamp [usec]');
    title('diff timestamp');

    % flight modes
    mode_values = ac_data.ROTORCRAFT_RADIO_CONTROL.mode;
    mode_timestamps = ac_data.ROTORCRAFT_RADIO_CONTROL.timestamp;
    cycl_draw_mode_transitions(mode_values, mode_timestamps, {ax1,ax2,ax3});
    legend(ax1, [h1], {'ned x'});
    legend(ax2, [h2], {'ned y'});
    legend(ax3, [h3], {'ned z'});
    legend(ax4, [h4], {'diff timestamp'});

    linkaxes([ax1,ax2,ax3,ax4],'x');
    
end