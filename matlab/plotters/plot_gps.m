function plot_gps(ac_data, vert)
    if ~isfield(ac_data, 'GPS_INT')
        return
    end
    
    % Plot the GPS/position status
    ax1 = subplot(3,1,1);
    plot(ac_data.GPS_INT.timestamp, [ac_data.GPS_INT.fix, ac_data.GPS_INT.numsv]);
    if exist('vert','var')
        hold on;
        plot([vert'; vert'], repmat(ylim',1,size(vert,1)), '--r');
    end
    title('GPS status');
    xlabel('Time [s]');
    legend('Fix', 'Number of SV');

    ax2 = subplot(3,1,2);
    plot(ac_data.GPS_INT.timestamp, [ac_data.GPS_INT.pacc, ac_data.GPS_INT.pdop, ac_data.GPS_INT.sacc]);
    if exist('vert','var')
        hold on;
        plot([vert'; vert'], repmat(ylim',1,size(vert,1)), '--r');
    end
    title('Errors');
    xlabel('Time [s]');
    legend('Position accuracy', 'PDOP', 'Speed accuracy');

    subplot(3,1,3);
    plot3(ac_data.GPS_INT.ecef_x, ac_data.GPS_INT.ecef_y, ac_data.GPS_INT.ecef_z);
    title('Position (ECEF)');

    sgtitle('GPS')
    linkaxes([ax1,ax2],'x')
end