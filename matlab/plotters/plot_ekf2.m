function plot_ekf2(ac_data, vert)
    if ~isfield(ac_data, 'INS_EKF2')
        return
    end
    
    % Plot the EKF2 status
    ax1 = subplot(2,1,1);
    plot(ac_data.INS_EKF2.timestamp, [ac_data.INS_EKF2.innov_mag, ac_data.INS_EKF2.innov_pos, ac_data.INS_EKF2.innov_vel, ac_data.INS_EKF2.innov_hagl]);
    if exist('vert','var')
        hold on;
        plot([vert'; vert'], repmat(ylim',1,size(vert,1)), '--r');
    end
    title('EKF2 innovations');
    xlabel('Time [s]');
    legend('Magnetometer', 'Position ', 'Velocity', 'Height AGL');

    ax2 = subplot(2,1,2);
    plot(ac_data.GPS_INT.timestamp, [ac_data.GPS_INT.pacc, ac_data.GPS_INT.pdop, ac_data.GPS_INT.sacc]);
    if exist('vert','var')
        hold on;
        plot([vert'; vert'], repmat(ylim',1,size(vert,1)), '--r');
    end
    title('Errors');
    xlabel('Time [s]');
    legend('Position accuracy', 'PDOP', 'Speed accuracy');

    sgtitle('INS EKF2')
    linkaxes([ax1,ax2],'x')
end