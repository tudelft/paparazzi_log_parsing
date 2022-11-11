function plot_rotorcraft_cmd(ac_data, vert)
    if ~isfield(ac_data, 'ROTORCRAFT_CMD')
        return
    end
    
    % Plot the Rotorcraft CMD
    plot(ac_data.ROTORCRAFT_CMD.timestamp, [ac_data.ROTORCRAFT_CMD.cmd_roll, ac_data.ROTORCRAFT_CMD.cmd_pitch, ac_data.ROTORCRAFT_CMD.cmd_yaw, ac_data.ROTORCRAFT_CMD.cmd_thrust]);
    if exist('vert','var')
        hold on;
        plot([vert'; vert'], repmat(ylim',1,size(vert,1)), '--r');
    end
    title('Commands');
    xlabel('Time [s]');
    legend('Roll [deg]', 'Pitch [deg]', 'Yaw [deg]', 'Thrust [deg]');
    
    sgtitle('Rotorcraft CMD measurements')
end