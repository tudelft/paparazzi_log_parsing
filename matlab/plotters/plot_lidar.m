function plot_lidar(ac_data, vert)
    if ~isfield(ac_data, 'LIDAR')
        return
    end
    
    % Plot the Energy message
    ax1 = subplot(2,1,1);
    yyaxis left
    plot(ac_data.LIDAR.timestamp, [ac_data.LIDAR.distance]);
    ylabel('Distance [m]');
    yyaxis right
    plot(ac_data.LIDAR.timestamp, [ac_data.LIDAR.status]);
    ylabel('Status [-]');

    if exist('vert','var')
        hold on;
        plot([vert'; vert'], repmat(ylim',1,size(vert,1)), '--r');
    end
    title('Energy Voltage');
    xlabel('Time [s]');
    legend('Voltage [V]', 'Current [A]');

    ax2 = subplot(2,1,2);
    plot(ac_data.ENERGY.timestamp, [ac_data.ENERGY.power]);
    if exist('vert','var')
        hold on;
        plot([vert'; vert'], repmat(ylim',1,size(vert,1)), '--r');
    end
    title('Energy Power');
    xlabel('Time [s]');
    legend('Power [W]');

    sgtitle('Energy')
    linkaxes([ax1, ax2],'x')
end