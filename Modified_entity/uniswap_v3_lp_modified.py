from dataclasses import dataclass, field
from typing import List
import numpy as np

from fractal.core.base.entity import EntityException
from fractal.core.entities.models.uniswap_v3_fees import (estimate_fee,
                                                          get_liquidity_delta)
from fractal.core.entities.pool import BasePoolEntity


@dataclass
class UniswapV3LPGlobalState:
    """
    Represents the global state of the UniswapV3 LP entity.

    Attributes:
        tvl (float): The total value locked.
        volume (float): The trading volume.
        fees (float): The trading fees.
        liquidity (float): The pool liquidity.
        price (float): The pool price [token1 / token0].
    """
    tvl: float = 0.0
    volume: float = 0.0
    fees: float = 0.0
    liquidity: float = 0.0
    price: float = 0.0

@dataclass 
class Position:
    token0_amount: float = 0.0
    token1_amount: float = 0.0
    fees: float = 0.0
    price_lower: float = 0.0
    price_upper: float = 0.0
    liquidity: float = 0.0


@dataclass
class UniswapV3LPInternalState:
    """
    Represents the internal state of an UniswapV3 LP entity.

    Attributes:
        token0_amount (float): The amount of token0.
        token1_amount (float): The amount of token1.
        price_init (float): The position initial price.
        price_lower (float): The range lower price.
        price_upper (float): The range upper price.
        liquidity (float): The position liquidity.
        cash (float): The cash balance.
    """
    positions: List[Position] = field(default_factory=list)
    cash: float = 0.0


@dataclass
class UniswapV3LPConfig:
    """
    Represents the configuration of an UniswapV3 LP entity.

    Attributes:
        fees_rate (float): The fees rate.
        token0_decimals (int): The token0 decimals.
        token1_decimals (int): The token1 decimals.
        trading_fee (float): The trading fee while opening/closing a position.
    """
    fees_rate: float = 0.005
    token0_decimals: int = 18
    token1_decimals: int = 18
    trading_fee: float = 0.003


class UniswapV3LPEntity(BasePoolEntity):
    """
    Represents an Uniswap V3 LP entity.

    It maintains single position in the V3 pool.
    """
    def __init__(self, config: UniswapV3LPConfig, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.is_position: bool = False
        self.fees_rate: float = config.fees_rate
        self.token0_decimals: int = config.token0_decimals
        self.token1_decimals: int = config.token1_decimals
        self.trading_fee: float = config.trading_fee

    def _initialize_states(self):
        self._internal_state = UniswapV3LPInternalState()
        self._global_state = UniswapV3LPGlobalState()

    def action_deposit(self, amount_in_notional: float) -> None:
        """
        Deposit funds into the LP entity.

        Args:
            amount_in_notional (float): The amount to deposit.
        """
        self._internal_state.cash += amount_in_notional

    def action_withdraw(self, amount_in_notional: float) -> None:
        """
        Withdraw funds from the LP entity.

        Args:
            amount_in_notional (float): The amount to withdraw.
        """
        if amount_in_notional > self._internal_state.cash:
            raise EntityException("Insufficient funds to withdraw.")
        self._internal_state.cash -= amount_in_notional

    def action_open_position(self, amount_in_notional: float, price_lower: float, price_upper: float) -> None:
        """
        Open a position in the LP entity.

        Args:
            amount_in_notional (float): The amount to invest.
            price_lower (float): The lower price of the range.
            price_upper (float): The upper price of the range.
        """
        if amount_in_notional > self._internal_state.cash:
            raise EntityException("Insufficient funds to open position.")
        new_position = Position()
        self._internal_state.cash -= amount_in_notional
        amount_in_position = amount_in_notional
        self.is_position = True
        new_position = self.calculate_position_from_notional(
            deposit_amount_in_notional=amount_in_position,
            price_current=self._global_state.price,
            price_upper=price_upper,
            price_lower=price_lower,
        )
        self._internal_state.positions.append(new_position)

    def action_close_position(self):
        """
        Close the position in the LP entity.
        """
        if not self.is_position:
            raise EntityException("No position to close.")
        cash = self.balance * (1 - self.trading_fee)
        print(cash)
        self.is_position = False
        self._internal_state.positions.clear()
        self._internal_state.cash = cash

    def update_state(self, state: UniswapV3LPGlobalState) -> None:
        """
        Update the state of the LP entity.

        1. Update the global state.
        2. Update token0 and token1 amounts following Uniswap V3 formula.
        3. Calculate fees and add to cash balance.

        Args:
            state (UniswapV3LPGlobalState): The state of the pool.
        """
        self._global_state = state
        if not self.is_position:
            return
        p = state.price

        for position in self._internal_state.positions:
            pl = position.price_lower
            pu = position.price_upper
            if p <= pl:
                position.token0_amount = 0
                position.token1_amount = position.liquidity * (1 / (pl**0.5) - 1 / (pu**0.5))
            elif pl < p < pu:
                position.token0_amount = position.liquidity * (p**0.5 - pl**0.5)
                position.token1_amount = position.liquidity * (1 / (p**0.5) - 1 / (pu**0.5))
            else:
                position.token0_amount = position.liquidity * (pu**0.5 - pl**0.5)
                position.token1_amount = 0
            print(self.calculate_fees(position))
            self._internal_state.cash += self.calculate_fees(position)

    @property
    def balance(self) -> float:
        """
        Returns the balance of the LP entity.

        Returns:
            float: The balance of the LP entity.
        """
        if not self.is_position:
            return self._internal_state.cash
        return (
            sum(self._internal_state.positions[i].token0_amount for i in range(len(self._internal_state.positions))) 
            + sum(self._internal_state.positions[i].token1_amount for i in range(len(self._internal_state.positions))) * self._global_state.price
            + self._internal_state.cash
        )

    def get_desired_token0_amount(
        self, deposit_amount: float, price_current: float, price_lower: float, price_upper: float
    ) -> float:
        """
        Returns desired token0 amount for position

        Args:
            deposit_amount (float): deposited token amount in token1
            price_current (float): token1/token0 price
            price_upper (float): upper price bound
            price_lower (float): lower price bound

        Returns:
            desired_token0_amount (float): desired token0 amount for position
        """
        if price_lower >= price_upper:
            raise EntityException(f"price_lower must be less than price_upper - {price_lower} >= {price_upper}")
        if price_current < price_lower or price_current > price_upper:
            raise EntityException("price_current must be in [price_lower, price_upper]")
        if deposit_amount <= 0:
            raise EntityException("deposit_amount must be positive")
        if price_current <= 0:
            raise EntityException("price_current must be positive")
        if price_upper <= 0:
            raise EntityException("price_upper must be positive")
        if price_lower <= 0:
            raise EntityException("price_lower must be positive")

        # provide liquidity by the token1 amount
        liquidity = deposit_amount / (1 / (price_current**0.5) - 1 / (price_upper**0.5))
        token0_amount = liquidity * (price_current**0.5 - price_lower**0.5)
        return token0_amount

    def calculate_position(
        self, deposit_amount: float, price_current: float, price_lower: float, price_upper: float
    ) -> Position:
        """
        Add position to positions dict

        Args:
            deposit_amount (float): deposited token amount in token1
            price_current (float): token1/token0 price
            price_upper (float): upper price bound
            price_lower (float): lower price bound
        """

        if price_lower >= price_upper:
            raise EntityException(f"price_lower must be less than price_upper - {price_lower} >= {price_upper}")
        if price_current < price_lower or price_current > price_upper:
            raise EntityException("price_current must be in [price_lower, price_upper]")
        if deposit_amount <= 0:
            raise EntityException("deposit_amount must be positive")
        if price_current <= 0:
            raise EntityException("price_current must be positive")
        if price_upper <= 0:
            raise EntityException("price_upper must be positive")
        if price_lower <= 0:
            raise EntityException("price_lower must be positive")
        
        new_position = Position()

        token1_amount = deposit_amount * (1 - self.trading_fee)
        liquidity = deposit_amount / (1 / (price_current**0.5) - 1 / (price_upper**0.5))
        token0_amount = liquidity * (price_current**0.5 - price_lower**0.5)

        if token0_amount <= 0:
            raise EntityException("token0_amount must be positive")
        if token1_amount <= 0:
            raise EntityException("token1_amount must be positive")
        if liquidity <= 0:
            raise EntityException("liquidity must be positive")

        new_position.token0_amount = token0_amount
        new_position.token1_amount = token1_amount
        new_position.price_lower = price_lower
        new_position.price_upper = price_upper
        new_position.liquidity = liquidity

        return new_position
        


    def calculate_position_from_notional(
        self,
        deposit_amount_in_notional: float,
        price_current: float,
        price_lower: float,
        price_upper: float,
    ) -> Position:
        """
        Add a new position by notional amount (token1) using Uniswap V3 formulas
        
        Args:
            deposit_amount_in_notional (float): Deposited amount in token1
            price_current (float): Current price (token1/token0)
            price_upper (float): Upper price bound
            price_lower (float): Lower price bound
        """
        # Convert to sqrt prices as per Uniswap V3 convention
        sqrt_lower = np.sqrt(price_lower)
        sqrt_upper = np.sqrt(price_upper)
        
        # Calculate liquidity based on price position
        if price_current <= price_lower:
            deposit_amount_in_notional *= (1 - self.trading_fee)
            L = deposit_amount_in_notional / (1/sqrt_lower - 1/sqrt_upper) / price_current
            token0_amount = 0.0
            token1_amount = deposit_amount_in_notional / price_current
            return Position(
                token0_amount=token0_amount,
                token1_amount=token1_amount,
                price_lower=price_lower,
                price_upper=price_upper,
                liquidity=L
            )
        elif price_current >= price_upper:
            token0_amount = deposit_amount_in_notional
            token1_amount = 0.0
            L = token0_amount / (sqrt_upper - sqrt_lower)
            return Position(
                token0_amount=token0_amount,
                token1_amount=token1_amount,
                price_lower=price_lower,
                price_upper=price_upper,
                liquidity=L
            )
        else:
            X = deposit_amount_in_notional

            token0 = self.get_desired_token0_amount(
                deposit_amount=X / 2 / price_current,
                price_current=price_current,
                price_upper=price_upper,
                price_lower=price_lower,
            )
            ratio = token0 / (X / 2 / price_current)
            position = self.calculate_position(
                deposit_amount=X / (ratio + price_current),
                price_current=price_current,
                price_upper=price_upper,
                price_lower=price_lower,
            )

            return position
    


    def calculate_fees(self, position: Position) -> float:
        """

        Args:
            position (UniswapV3Position): position to which calc fees
            pool_state (PoolState): pool state

        Returns:
            float: acc fees for position
        """

        # revert prices cuase we need token0/token1 price
        # and our model works with token1/token0 price
        p = self._global_state.price
        pl = position.price_lower
        pu = position.price_upper

        delta_liquidity = get_liquidity_delta(
            P=(1 / p),
            lower_price=(1 / pu),
            upper_price=(1 / pl),
            amount0=position.token0_amount,
            amount1=position.token1_amount,
            token0_decimal=self.token0_decimals,
            token1_decimal=self.token1_decimals,
        )

        # if price is out of range then fees are 0
        if p <= pl or p >= pu:
            return 0

        fees = estimate_fee(
            liquidity_delta=delta_liquidity,
            liquidity=self._global_state.liquidity,
            fees=self._global_state.fees,
        )
        
        return min(fees, self._global_state.fees)

    def price_to_tick(self, price: float) -> float:
        return np.floor(np.log(price) / np.log(1.0001))

    def tick_to_price(self, tick: float) -> float:
        return 1.0001**tick