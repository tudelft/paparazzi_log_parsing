function plot_guidance(ac_data)
    if ~isfield(ac_data, 'GUIDANCE')
        return
    end
 
    % Plot the Euler angles
    msg = ac_data.GUIDANCE;

    ax1 = subplot(3,1,1);
    hold on;
    plot(msg.timestamp,msg.pos_N,msg.timestamp,msg.pos_N_ref);
    plot(msg.timestamp,msg.pos_E,msg.timestamp,msg.pos_E_ref);
    plot(msg.timestamp,msg.pos_D,msg.timestamp,msg.pos_D_ref);
    if exist('vert','var')
        hold on;
        plot([vert'; vert'], repmat(ylim',1,size(vert,1)), '--r');
    end
    title('Position'); xlabel('time(s)'); ylabel('Position [m]'); legend('pos N', 'ref N', 'pos E', 'ref E', 'pos D', 'ref D')

    ax2 = subplot(3,1,2);
    hold on;
    plot(msg.timestamp,msg.vel_N,msg.timestamp,msg.vel_N_ref);
    plot(msg.timestamp,msg.vel_E,msg.timestamp,msg.vel_E_ref);
    plot(msg.timestamp,msg.vel_D,msg.timestamp,msg.vel_D_ref);
    if exist('vert','var')
        hold on;
        plot([vert'; vert'], repmat(ylim',1,size(vert,1)), '--r');
    end
    title('Velocity'); xlabel('time(s)'); ylabel('Velocity [m/s]'); legend('vel N', 'ref N', 'vel E', 'ref E', 'vel D', 'ref D')
    
    colors = get(gca,'colororder');

    ax3 = subplot(3,1,3);
    hold on;
    plot(msg.timestamp,msg.acc_N, 'Color', colors(1,:))
    plot(msg.timestamp,msg.acc_E, 'Color', colors(3,:))
    plot(msg.timestamp,msg.acc_D, 'Color', colors(5,:))
    plot(msg.timestamp,msg.acc_N_ref, 'Color', colors(2,:))
    plot(msg.timestamp,msg.acc_E_ref, 'Color', colors(4,:))
    plot(msg.timestamp,msg.acc_D_ref, 'Color', colors(6,:))
    title('Acceleration'); xlabel('time(s)'); ylabel('Acceleration [m/s^2]'); legend('acc N', 'acc E', 'acc D', 'ref N', 'ref E', 'ref D')

    sgtitle(['Guidance'])
    linkaxes([ax1,ax2,ax3],'x')
end