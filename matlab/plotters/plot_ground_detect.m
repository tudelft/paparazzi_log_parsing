function plot_ground_detect(ac_data)
    
    
    if isfield(ac_data, 'PAYLOAD_FLOAT')
        gd = double(string(ac_data.PAYLOAD_FLOAT.values));
        t = ac_data.PAYLOAD_FLOAT.timestamp;

        ax1 = subplot(5,1,1);
        hold on
        plot(t, gd(:,1))
        plot(t, 5*ones(size(t)), 'k--')
        title('vspeed ned')
        ax2 = subplot(5,1,2);
        hold on;
        plot(t, gd(:,2))
        plot(t, -5*ones(size(t)), 'c--')
        title('spec_thrust_down')
        ax3 = subplot(5,1,3);
        hold on;
        plot(t, gd(:,4), t, gd(:,3))
        plot(t, 2*ones(size(t)), 'k--')
        legend('accel','accel filt')
        title('accel ned z')
        ax4 = subplot(5,1,4);
        hold on;
        plot(t, gd(:,5:6))
        plot(t, 0.24*ones(size(t)), 'k--')
        legend('agl valid','agl value filt')
        title('agl')
        ax5 = subplot(5,1,5);
        plot(t, gd(:,7))
        xlabel('Time [s]');
        title('ground detected')

        sgtitle('Ground detect')
        linkaxes([ax1, ax2, ax3, ax4, ax5],'x')
    end
end