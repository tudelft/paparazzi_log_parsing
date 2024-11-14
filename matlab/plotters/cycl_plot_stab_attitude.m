function plot_stab_attitude(ac_data, order)
    if ~isfield(ac_data, 'STAB_ATTITUDE')
        return
    end
asfc
    att = double(string(ac_data.STAB_ATTITUDE.att));
    attref = double(string(ac_data.STAB_ATTITUDE.att_ref));

    w = double(string(ac_data.STAB_ATTITUDE.angular_rate));
    wref = double(string(ac_data.STAB_ATTITUDE.angular_rate_ref));

    wdot = double(string(ac_data.STAB_ATTITUDE.angular_accel));
    wdotref = double(string(ac_data.STAB_ATTITUDE.angular_accel_ref));

    ax1 = subplot(3,1,1);
    hold on; zoom on;
    plot(ac_data.STAB_ATTITUDE.timestamp, wdot(:,1), 'color', '#0072BD');
    plot(ac_data.STAB_ATTITUDE.timestamp, wdotref(:,1), '--', 'color', '#0072BD');
    plot(ac_data.STAB_ATTITUDE.timestamp, wdot(:,2), 'color', '#D95319');
    plot(ac_data.STAB_ATTITUDE.timestamp, wdotref(:,2), '--', 'color', '#D95319');
    plot(ac_data.STAB_ATTITUDE.timestamp, wdot(:,3), 'color', '#EDB120');
    plot(ac_data.STAB_ATTITUDE.timestamp, wdotref(:,3), '--', 'color', '#EDB120');
    title('angular acceleration'); xlabel('time(s)'); ylabel('(rad/s^2)'); legend('pdot','pdotref','qdot','qdotref','rdot','rdotref')

    ax2 = subplot(3,1,2);
    hold on; zoom on;
    plot(ac_data.STAB_ATTITUDE.timestamp, w(:,1), 'color', '#0072BD');
    plot(ac_data.STAB_ATTITUDE.timestamp, wref(:,1), '--', 'color', '#0072BD');
    plot(ac_data.STAB_ATTITUDE.timestamp, w(:,2), 'color', '#D95319');
    plot(ac_data.STAB_ATTITUDE.timestamp, wref(:,2), '--', 'color', '#D95319');
    plot(ac_data.STAB_ATTITUDE.timestamp, w(:,3), 'color', '#EDB120');
    plot(ac_data.STAB_ATTITUDE.timestamp, wref(:,3), '--', 'color', '#EDB120');
    title('angular rates'); xlabel('time(s)'); ylabel('(rad/s)'); legend('p','pref','q','qref','r','rref')

    ax3 = subplot(3,1,3);
    hold on; zoom on;
    plot(ac_data.STAB_ATTITUDE.timestamp, att(:,1), 'color', '#0072BD');
    plot(ac_data.STAB_ATTITUDE.timestamp, attref(:,1), '--', 'color', '#0072BD');
    plot(ac_data.STAB_ATTITUDE.timestamp, att(:,2), 'color', '#D95319');
    plot(ac_data.STAB_ATTITUDE.timestamp, attref(:,2), '--', 'color', '#D95319');
    plot(ac_data.STAB_ATTITUDE.timestamp, att(:,3), 'color', '#EDB120');
    plot(ac_data.STAB_ATTITUDE.timestamp, attref(:,3), '--', 'color', '#EDB120');
    title('Euler angles'); xlabel('time(s)'); ylabel('(rad)'); legend('phi','phiref','theta','thetaref','psi','psiref')

    sgtitle(['Stab attitude'])
    linkaxes([ax1,ax2,ax3],'x')
end