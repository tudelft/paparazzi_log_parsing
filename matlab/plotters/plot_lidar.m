function plot_lidar(ac_data, vert)
    if ~isfield(ac_data, 'LIDAR')
        return
    end
    
    % Plot the Energy message
    plot(ac_data.LIDAR.timestamp, [ac_data.LIDAR.distance]);
    ylabel('Distance [m]');
    ylim([0 10])
    
    if exist('vert','var')
        hold on;
        plot([vert'; vert'], repmat(ylim',1,size(vert,1)), '--r');
    end
    title('Lidar distance');
    xlabel('Time [s]');
end